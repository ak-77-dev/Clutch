import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { desktop, isDesktop, useDesktopEvents, type MediaAction, type MediaSession, type MediaState } from '../desktop'
import { fmtClock } from '../format'
import { MusicIcon, NextIcon, PauseIcon, PlayIcon, PrevIcon, RepeatIcon, ShuffleIcon } from './Icons'
import { useDesktop } from './Shell'

/** Live media sessions: fetched once, then kept current by the backend's `media` events. */
export function useMedia() {
  const { toast } = useDesktop()
  const [state, setState] = useState<MediaState | null>(null)
  useEffect(() => {
    if (!isDesktop) return
    desktop.media().then(setState, () => {})
  }, [])
  useDesktopEvents((e) => {
    if (e.type === 'media') setState(e)
  })
  const run = useCallback(
    async (action: MediaAction, session?: string | null, value?: unknown) => {
      // Flip play / pause right away; the backend answers once the app has actually changed.
      if (action === 'play_pause' || action === 'play' || action === 'pause') setState((st) => (st ? optimistic(st, action, session) : st))
      try {
        setState(await desktop.mediaCommand(action, session, value))
      } catch (e) {
        toast({ title: 'Music', sub: (e as Error).message, tone: 'error' })
      }
    },
    [toast],
  )
  return { state, setState, run }
}

function optimistic(st: MediaState, action: MediaAction, session?: string | null): MediaState {
  const target = session ?? st.sessions.find((x) => x.title)?.id
  const now = Date.now() / 1000
  return {
    ...st,
    at: now,
    sessions: st.sessions.map((s) => {
      if (s.id !== target) return s
      const playing = action === 'play' || (action === 'play_pause' && s.status !== 'playing')
      const position = s.status === 'playing' ? Math.min(s.duration || Infinity, s.position + Math.max(0, now - st.at)) : s.position
      return { ...s, status: playing ? 'playing' : 'paused', position }
    }),
  }
}

/** Where the track is now: sessions report a position as of the last update, so playing ones tick forward. */
export function usePosition(s: MediaSession | null | undefined, at: number | undefined): number {
  const [now, setNow] = useState(() => Date.now() / 1000)
  const playing = s?.status === 'playing'
  useEffect(() => {
    if (!playing) return
    const id = setInterval(() => setNow(Date.now() / 1000), 500)
    return () => clearInterval(id)
  }, [playing])
  if (!s) return 0
  const pos = playing && at ? s.position + Math.max(0, now - at) : s.position
  return s.duration ? Math.min(pos, s.duration) : pos
}

export function Art({ s, size = 44, className = '' }: { s: MediaSession | null | undefined; size?: number; className?: string }) {
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [s?.art])
  return (
    <div className={`art ${className}`} style={{ width: size, height: size, '--app': appAccent(s?.app) } as React.CSSProperties}>
      {s?.art && !failed ? <img src={desktop.mediaArt(s.art)} alt="" onError={() => setFailed(true)} /> : <MusicIcon />}
    </div>
  )
}

export function appAccent(app: string | undefined): string {
  return { spotify: '#1ed760', applemusic: '#fa2d48', ytmusic: '#ff0033' }[app ?? ''] ?? 'var(--volt)'
}

export function Transport({ s, run, big = false }: { s: MediaSession; run: (a: MediaAction, session?: string | null, v?: unknown) => void; big?: boolean }) {
  const playing = s.status === 'playing'
  const nextRepeat = { none: 'list', list: 'track', track: 'none' } as const
  return (
    <div className={`transport ${big ? 'big' : ''}`}>
      {big && s.can.shuffle && (
        <button className={`tbtn ghost ${s.shuffle ? 'on' : ''}`} aria-pressed={Boolean(s.shuffle)} title="Shuffle" onClick={() => run('shuffle', s.id, !s.shuffle)}>
          <ShuffleIcon />
        </button>
      )}
      <button className="tbtn" disabled={!s.can.previous} title="Previous" onClick={() => run('previous', s.id)}>
        <PrevIcon />
      </button>
      <button className="tbtn play" disabled={!s.can.play_pause} title={playing ? 'Pause' : 'Play'} onClick={() => run('play_pause', s.id)}>
        {playing ? <PauseIcon /> : <PlayIcon />}
      </button>
      <button className="tbtn" disabled={!s.can.next} title="Next" onClick={() => run('next', s.id)}>
        <NextIcon />
      </button>
      {big && s.can.repeat && (
        <button
          className={`tbtn ghost ${s.repeat && s.repeat !== 'none' ? 'on' : ''}`}
          title={`Repeat: ${s.repeat ?? 'none'}`}
          onClick={() => run('repeat', s.id, nextRepeat[s.repeat ?? 'none'])}
        >
          <RepeatIcon />
          {s.repeat === 'track' && <span className="one">1</span>}
        </button>
      )}
    </div>
  )
}

/** A progress bar you can click or drag to seek (when the app allows it). */
export function SeekBar({ s, at, run }: { s: MediaSession; at: number; run: (a: MediaAction, session?: string | null, v?: unknown) => void }) {
  const pos = usePosition(s, at)
  const bar = useRef<HTMLDivElement>(null)
  const [drag, setDrag] = useState<number | null>(null)
  // After a seek, hold the new spot until the app reports it (instead of snapping back for a moment).
  const [pending, setPending] = useState<{ to: number; at: number } | null>(null)
  const pendingNow = pending ? pending.to + (s.status === 'playing' ? Date.now() / 1000 - pending.at : 0) : null
  useEffect(() => {
    if (!pending) return
    if (Math.abs(pos - pending.to) < 2.5 || Date.now() / 1000 - pending.at > 3) setPending(null)
  }, [pos, pending])
  useEffect(() => setPending(null), [s.id, s.title])
  const shown = drag ?? pendingNow ?? pos
  const pct = s.duration ? (shown / s.duration) * 100 : 0

  function at_(x: number) {
    const el = bar.current!
    const r = el.getBoundingClientRect()
    return Math.min(s.duration, Math.max(0, ((x - r.left) / r.width) * s.duration))
  }
  function down(e: React.PointerEvent) {
    if (!s.can.seek || !s.duration) return
    e.preventDefault()
    setDrag(at_(e.clientX))
    const move = (ev: PointerEvent) => setDrag(at_(ev.clientX))
    const up = (ev: PointerEvent) => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      const to = at_(ev.clientX)
      setDrag(null)
      setPending({ to, at: Date.now() / 1000 })
      run('seek', s.id, Math.round(to * 10) / 10)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }
  return (
    <div className="seek">
      <span>{fmtClock(shown)}</span>
      <div className={`seek-bar ${s.can.seek ? 'can' : ''}`} ref={bar} onPointerDown={down} role="slider" aria-label="Seek" aria-valuemin={0} aria-valuemax={s.duration} aria-valuenow={Math.round(shown)}>
        <i style={{ width: `${pct}%` }} />
        <b style={{ left: `${pct}%` }} />
      </div>
      <span>{s.duration ? fmtClock(s.duration) : '—'}</span>
    </div>
  )
}

/** Compact player at the bottom of the rail: what's playing, with prev / play / next. */
export function MiniPlayer() {
  const { state, run } = useMedia()
  const s = state?.sessions.find((x) => x.title) ?? null
  const pos = usePosition(s, state?.at)
  if (!state?.available || !s) return null
  return (
    <div className={`mini-player ${s.status === 'playing' ? 'playing' : ''}`} style={{ '--app': appAccent(s.app) } as React.CSSProperties}>
      <Link to="/music" className="mp-info" title={`${s.title} — ${s.artist}`}>
        <Art s={s} size={38} />
        <div style={{ minWidth: 0 }}>
          <div className="mp-title">{s.title}</div>
          <div className="mp-sub">
            {s.status === 'playing' && (
              <span className="bars" aria-hidden>
                <i />
                <i />
                <i />
              </span>
            )}
            {s.artist || s.app_name}
          </div>
        </div>
      </Link>
      <Transport s={s} run={run} />
      <div className="mp-progress">
        {/* keyed by track, so a new song starts at 0 instead of sliding back from the old position */}
        <i key={`${s.id}|${s.title}`} style={{ width: `${s.duration ? (pos / s.duration) * 100 : 0}%` }} />
      </div>
    </div>
  )
}
