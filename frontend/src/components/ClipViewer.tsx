import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { bridge, desktop, type Clip, type ClipMatch } from '../desktop'
import { fmtBytes, fmtClock } from '../format'
import { useGames } from '../hooks'
import {
  ChevronLeft,
  ChevronRight,
  CloseIcon,
  DiscordIcon,
  FolderIcon,
  GifIcon,
  LinkIcon,
  MicOffIcon,
  PhoneIcon,
  ScissorsIcon,
  ShareIcon,
  StarIcon,
  TextIcon,
  TrashIcon,
} from './Icons'
import { useDesktop } from './Shell'

const HOSTS = { catbox: 'catbox.moe (public, permanent)', litterbox: 'litterbox.catbox.moe (public, deleted after 72 hours)' }

function copyText(text: string) {
  if (bridge?.copy) bridge.copy(text)
  else void navigator.clipboard?.writeText(text)
}

function openUrl(url: string) {
  if (bridge?.openExternal) bridge.openExternal(url)
  else window.open(url, '_blank', 'noopener')
}

/**
 * Full-screen clip viewer with an in/out trim range.
 * Keys: Space play/pause · I / O set in/out · ← → step 5 s · , . previous / next clip · Esc close.
 */
export function ClipViewer({
  clip,
  onClose,
  onPrev,
  onNext,
  onChanged,
}: {
  clip: Clip
  onClose: () => void
  onPrev?: () => void
  onNext?: () => void
  onChanged: (next?: number | null) => void
}) {
  const video = useRef<HTMLVideoElement>(null)
  const bar = useRef<HTMLDivElement>(null)
  const { toast, settings } = useDesktop()
  const games = useGames()
  const [caption, setCaption] = useState('')
  const [capPos, setCapPos] = useState<'top' | 'bottom'>('bottom')
  const [shareUrl, setShareUrl] = useState<string | null>(clip.share_url ?? null)
  const [match, setMatch] = useState<ClipMatch | null>(null)
  const [title, setTitle] = useState(clip.title)
  const [fav, setFav] = useState(clip.favorite)
  const [t, setT] = useState(0)
  const [range, setRange] = useState<[number, number]>([0, clip.duration ?? 0])
  const [busy, setBusy] = useState<string | null>(null)
  const dur = clip.duration ?? 0
  const isVideo = clip.kind !== 'screenshot' && !clip.path.toLowerCase().endsWith('.gif')
  const trimmed = range[0] > 0.05 || range[1] < dur - 0.05

  useEffect(() => {
    setTitle(clip.title)
    setFav(clip.favorite)
    setRange([0, clip.duration ?? 0])
    setT(0)
    setShareUrl(clip.share_url ?? null)
    setCaption('')
  }, [clip.id, clip.title, clip.favorite, clip.duration, clip.share_url])

  // Which match this clip is from (only when the game's stats account is linked).
  useEffect(() => {
    setMatch(null)
    if (clip.kind === 'screenshot') return
    let live = true
    desktop.clipMatch(clip.id).then(
      (r) => live && setMatch(r.match),
      () => {},
    )
    return () => {
      live = false
    }
  }, [clip.id, clip.kind])

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.target as HTMLElement).tagName === 'INPUT') return
      const v = video.current
      if (e.key === 'Escape') onClose()
      else if (e.key === ',' && onPrev) onPrev()
      else if (e.key === '.' && onNext) onNext()
      else if (!v) return
      else if (e.key === ' ') {
        e.preventDefault()
        void (v.paused ? v.play() : v.pause())
      } else if (e.key === 'ArrowLeft') v.currentTime = Math.max(0, v.currentTime - 5)
      else if (e.key === 'ArrowRight') v.currentTime = Math.min(dur, v.currentTime + 5)
      else if (e.key.toLowerCase() === 'i') setRange(([, b]) => [Math.min(v.currentTime, b - 0.5), b])
      else if (e.key.toLowerCase() === 'o') setRange(([a]) => [a, Math.max(v.currentTime, a + 0.5)])
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, onPrev, onNext, dur])

  // Keep playback inside the selected range so you preview exactly what you'll export.
  useEffect(() => {
    const v = video.current
    if (v && trimmed && (t >= range[1] || t < range[0] - 0.2)) v.currentTime = range[0]
  }, [t, range, trimmed])

  function drag(which: 0 | 1 | 'seek') {
    return (e: React.PointerEvent) => {
      e.preventDefault()
      e.stopPropagation()
      const el = bar.current
      if (!el || !dur) return
      const at = (x: number) => Math.min(dur, Math.max(0, ((x - el.getBoundingClientRect().left) / el.clientWidth) * dur))
      const apply = (x: number) => {
        const v = at(x)
        if (which === 'seek') {
          if (video.current) video.current.currentTime = v
        } else setRange(([a, b]) => (which === 0 ? [Math.min(v, b - 0.5), b] : [a, Math.max(v, a + 0.5)]))
      }
      apply(e.clientX)
      const move = (ev: PointerEvent) => apply(ev.clientX)
      const up = () => {
        window.removeEventListener('pointermove', move)
        window.removeEventListener('pointerup', up)
      }
      window.addEventListener('pointermove', move)
      window.addEventListener('pointerup', up)
    }
  }

  async function run(name: string, fn: () => Promise<Clip | unknown>, done: string) {
    setBusy(name)
    try {
      const result = await fn()
      toast({ title: done, sub: (result as Clip)?.title })
      onChanged(result && (result as Clip).id ? (result as Clip).id : undefined)
    } catch (e) {
      toast({ title: `${name} failed`, sub: (e as Error).message, tone: 'error' })
    } finally {
      setBusy(null)
    }
  }

  async function share() {
    const host = settings?.share_host ?? 'catbox'
    const ok = window.confirm(
      `Upload “${clip.title}” (${fmtBytes(clip.size)}) to ${HOSTS[host]}?\n\nAnyone with the link can watch it, and Clutch can’t delete it for you afterwards.`,
    )
    if (!ok) return
    setBusy('Share')
    try {
      const updated = await desktop.share(clip.id)
      setShareUrl(updated.share_url ?? null)
      if (updated.share_url) copyText(updated.share_url)
      toast({ title: 'Uploaded · link copied', sub: updated.share_url ?? undefined })
      onChanged()
    } catch (e) {
      toast({ title: 'Upload failed', sub: (e as Error).message, tone: 'error' })
    } finally {
      setBusy(null)
    }
  }

  async function saveTitle() {
    if (title.trim() && title !== clip.title) {
      await desktop.updateClip(clip.id, { title })
      onChanged()
    }
  }

  const pct = (v: number) => `${dur ? (v / dur) * 100 : 0}%`
  return (
    <div className="viewer-backdrop" role="dialog" aria-modal="true" aria-label={clip.title} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="viewer-stage" onClick={(e) => e.target === e.currentTarget && onClose()}>
        <button className="icon-btn viewer-close" onClick={onClose} aria-label="Close">
          <CloseIcon />
        </button>
        {onPrev && (
          <button className="icon-btn viewer-nav prev" onClick={onPrev} aria-label="Previous clip">
            <ChevronLeft />
          </button>
        )}
        {onNext && (
          <button className="icon-btn viewer-nav next" onClick={onNext} aria-label="Next clip">
            <ChevronRight />
          </button>
        )}
        <div style={{ display: 'grid', justifyItems: 'center', width: '100%' }}>
          {isVideo ? (
            <video key={clip.id} ref={video} src={desktop.clipFile(clip.id)} controls autoPlay onTimeUpdate={(e) => setT(e.currentTarget.currentTime)} />
          ) : (
            <img className="shot" src={desktop.clipFile(clip.id)} alt={clip.title} />
          )}
          {isVideo && dur > 0 && (
            <div className="timeline" ref={bar} onPointerDown={drag('seek')} aria-label="Trim range">
              <div className="sel" style={{ left: pct(range[0]), width: `calc(${pct(range[1])} - ${pct(range[0])})` }} />
              <div className="grab" style={{ left: pct(range[0]) }} onPointerDown={drag(0)} role="slider" aria-label="Trim start" aria-valuenow={range[0]} />
              <div className="grab" style={{ left: pct(range[1]) }} onPointerDown={drag(1)} role="slider" aria-label="Trim end" aria-valuenow={range[1]} />
              <div className="head" style={{ left: pct(t) }} />
              <div className="ticks">
                <span>{fmtClock(range[0])}</span>
                <span>
                  {trimmed ? `${(range[1] - range[0]).toFixed(1)}s selected` : 'drag the edges or press I / O to trim'}
                </span>
                <span>{fmtClock(range[1])}</span>
              </div>
            </div>
          )}
        </div>
      </div>

      <aside className="viewer-side">
        <div>
          <div className="kicker bare" style={{ marginBottom: 8 }}>
            <b>{clip.kind}</b> · {clip.game_name ?? 'Desktop'}
          </div>
          <input
            className="title-input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => void saveTitle()}
            onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
            aria-label="Title"
          />
        </div>
        <div className="row">
          <button
            className={`icon-btn ${fav ? 'on' : ''}`}
            aria-pressed={fav}
            title="Favorite"
            onClick={async () => {
              setFav(!fav)
              await desktop.updateClip(clip.id, { favorite: !fav })
              onChanged()
            }}
          >
            <StarIcon filled={fav} />
          </button>
          <button className="icon-btn" title="Show in folder" onClick={() => void desktop.reveal(clip.id)}>
            <FolderIcon />
          </button>
          <span style={{ flex: 1 }} />
          <button
            className="icon-btn"
            title="Delete (to the Recycle Bin)"
            onClick={() => {
              if (window.confirm(`Move “${clip.title}” to the Recycle Bin?`)) void run('Delete', async () => desktop.deleteClip(clip.id).then(() => onChanged(null)), 'Moved to the Recycle Bin')
            }}
          >
            <TrashIcon />
          </button>
        </div>

        {isVideo && (
          <div>
            <h2 className="section-title">Edit & share</h2>
            <div className="action-list">
              <button className="btn primary" disabled={!trimmed || busy !== null} onClick={() => void run('Trim', () => desktop.trim(clip.id, range[0], range[1]), 'Trimmed copy saved')}>
                <ScissorsIcon /> {busy === 'Trim' ? 'Trimming…' : trimmed ? `Save trim (${(range[1] - range[0]).toFixed(1)}s)` : 'Select a range to trim'}
              </button>
              <button className="btn" disabled={busy !== null} onClick={() => void run('GIF', () => desktop.gif(clip.id, range[0], Math.min(range[1], range[0] + 30)), 'GIF exported')}>
                <GifIcon /> {busy === 'GIF' ? 'Rendering GIF…' : `Export GIF${range[1] - range[0] > 30 ? ' (first 30s)' : ''}`}
              </button>
              <button className="btn" disabled={busy !== null} onClick={() => void run('Export', () => desktop.compact(clip.id, range[0], range[1]), 'Discord-ready MP4 saved')}>
                <DiscordIcon /> {busy === 'Export' ? 'Encoding…' : 'Export under 10 MB (Discord)'}
              </button>
            </div>
          </div>
        )}

        {match && (
          <Link className={`clip-match ${match.result}`} to={`/${match.game}/p/${encodeURIComponent(match.key)}`}>
            <span className="res">{match.result === 'win' ? 'W' : match.result === 'loss' ? 'L' : '—'}</span>
            <span style={{ minWidth: 0 }}>
              <span className="t">
                {match.character}
                {match.score_line ? ` · ${match.score_line}` : ''}
              </span>
              <span className="s">
                {games.find((g) => g.id === match.game)?.name ?? match.game} · {match.mode}
                {match.map ? ` · ${match.map}` : ''}
              </span>
            </span>
            <span className="go">match →</span>
          </Link>
        )}

        {isVideo && (
          <div>
            <h2 className="section-title">Share</h2>
            {shareUrl ? (
              <div className="share-link">
                <LinkIcon />
                <span className="url" title={shareUrl}>
                  {shareUrl.replace(/^https:\/\//, '')}
                </span>
                <button
                  className="btn small"
                  onClick={() => {
                    copyText(shareUrl)
                    toast({ title: 'Link copied' })
                  }}
                >
                  Copy
                </button>
                <button className="btn small ghost" onClick={() => openUrl(shareUrl)}>
                  Open
                </button>
              </div>
            ) : (
              <>
                <button className="btn" style={{ width: '100%' }} disabled={busy !== null} onClick={() => void share()}>
                  <ShareIcon /> {busy === 'Share' ? 'Uploading…' : 'Get a share link'}
                </button>
                <p className="hint">Public upload to {(settings?.share_host ?? 'catbox') === 'catbox' ? 'catbox.moe' : 'litterbox (gone after 72 h)'}. Change it in Settings.</p>
              </>
            )}
          </div>
        )}

        {isVideo && (
          <div>
            <h2 className="section-title">Remix</h2>
            <div className="action-list">
              <div className="caption-box">
                <label className="field">
                  <TextIcon />
                  <input value={caption} maxLength={80} onChange={(e) => setCaption(e.target.value)} placeholder="Caption text" aria-label="Caption text" />
                </label>
                <div className="spread">
                  <div className="seg">
                    {(['top', 'bottom'] as const).map((p) => (
                      <button key={p} aria-pressed={capPos === p} onClick={() => setCapPos(p)}>
                        {p}
                      </button>
                    ))}
                  </div>
                  <button
                    className="btn small"
                    disabled={!caption.trim() || busy !== null}
                    onClick={() => void run('Caption', () => desktop.caption(clip.id, caption.trim(), capPos), 'Captioned copy saved')}
                  >
                    {busy === 'Caption' ? 'Rendering…' : 'Burn in'}
                  </button>
                </div>
              </div>
              <div className="row" style={{ gap: 6 }}>
                <button
                  className="btn"
                  style={{ flex: 1 }}
                  disabled={busy !== null}
                  title="The whole frame, centered on a blurred fill"
                  onClick={() => void run('9:16', () => desktop.vertical(clip.id, 'blur'), '9:16 copy saved')}
                >
                  <PhoneIcon /> {busy === '9:16' ? 'Rendering…' : '9:16 fit'}
                </button>
                <button
                  className="btn"
                  style={{ flex: 1 }}
                  disabled={busy !== null}
                  title="Crop the middle of the screen to fill a phone"
                  onClick={() => void run('Crop', () => desktop.vertical(clip.id, 'crop'), '9:16 crop saved')}
                >
                  <PhoneIcon /> {busy === 'Crop' ? 'Rendering…' : '9:16 crop'}
                </button>
              </div>
              <button
                className="btn"
                disabled={busy !== null}
                title="Works on clips saved with “Separate audio tracks” on"
                onClick={() => void run('Mic removal', () => desktop.withoutMic(clip.id), 'Copy without your mic saved')}
              >
                <MicOffIcon /> {busy === 'Mic removal' ? 'Remuxing…' : 'Copy without my mic'}
              </button>
            </div>
          </div>
        )}

        <div>
          <h2 className="section-title">Details</h2>
          <dl className="kv">
            <dt>Saved</dt>
            <dd>{new Date(clip.created_at * 1000).toLocaleString()}</dd>
            {dur > 0 && (
              <>
                <dt>Length</dt>
                <dd>{fmtClock(dur)}</dd>
              </>
            )}
            {clip.width && (
              <>
                <dt>Size</dt>
                <dd>
                  {clip.width}×{clip.height} · {fmtBytes(clip.size)}
                </dd>
              </>
            )}
            <dt>File</dt>
            <dd>{clip.path}</dd>
          </dl>
        </div>
        <p className="mono muted" style={{ fontSize: 10.5, margin: 0, lineHeight: 1.7 }}>
          SPACE play · I/O trim · ←/→ 5s · ,/. prev/next · ESC close
        </p>
      </aside>
    </div>
  )
}
