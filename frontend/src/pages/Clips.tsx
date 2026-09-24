import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ClipTile } from '../components/ClipTile'
import { ClipViewer } from '../components/ClipViewer'
import { CloseIcon, FilmIcon, FolderIcon, SearchIcon, StarIcon } from '../components/Icons'
import { useDesktop } from '../components/Shell'
import { desktop, useDesktopEvents, type Clip } from '../desktop'
import { fmtBytes, fmtHours } from '../format'
import { useAsync } from '../hooks'
import { DesktopOnly } from './DesktopOnly'

const KINDS = [
  { id: '', label: 'Everything' },
  { id: 'clip', label: 'Clips' },
  { id: 'recording', label: 'Recordings' },
  { id: 'screenshot', label: 'Screenshots' },
  { id: 'export', label: 'Exports' },
]

export function Clips() {
  return (
    <DesktopOnly feature="Your clips and recordings">
      <ClipsInner />
    </DesktopOnly>
  )
}

function groupByDay(clips: Clip[]): [string, Clip[]][] {
  const today = new Date().toDateString()
  const yesterday = new Date(Date.now() - 86_400_000).toDateString()
  const groups = new Map<string, Clip[]>()
  for (const c of clips) {
    const d = new Date(c.created_at * 1000)
    const key = d.toDateString() === today ? 'Today' : d.toDateString() === yesterday ? 'Yesterday' : d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })
    groups.set(key, [...(groups.get(key) ?? []), c])
  }
  return [...groups.entries()]
}

function ClipsInner() {
  const [params, setParams] = useSearchParams()
  const [kind, setKind] = useState('')
  const [favorites, setFavorites] = useState(false)
  const [q, setQ] = useState('')
  const game = params.get('game') ?? ''
  const openId = Number(params.get('open')) || null
  const clips = useAsync(() => desktop.clips({ kind, favorites, q: q.trim() || undefined }), [kind, favorites, q])
  const { status, settings, toast } = useDesktop()
  const [picking, setPicking] = useState(false)
  const [picked, setPicked] = useState<number[]>([])
  const [making, setMaking] = useState(false)

  useDesktopEvents((e) => {
    if (e.type === 'clip_saved' || e.type === 'clip_updated') clips.reload()
  })

  // Pick up clips saved while the app was closed (and forget deleted files) once per visit.
  useEffect(() => {
    desktop.importClips().then((r) => (r.added || r.removed) && clips.reload(), () => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const all = clips.data?.items ?? []
  const games = useMemo(() => {
    const m = new Map<string, { id: string; name: string; n: number }>()
    for (const c of all) {
      const id = c.game_id ?? `name:${c.game_name ?? 'Desktop'}`
      const cur = m.get(id) ?? { id, name: c.game_name ?? 'Desktop', n: 0 }
      m.set(id, { ...cur, n: cur.n + 1 })
    }
    return [...m.values()].sort((a, b) => b.n - a.n)
  }, [all])
  const shown = game ? all.filter((c) => (c.game_id ?? `name:${c.game_name ?? 'Desktop'}`) === game) : all
  const openIdx = openId ? shown.findIndex((c) => c.id === openId) : -1

  function setOpen(id: number | null) {
    const next = new URLSearchParams(params)
    if (id) next.set('open', String(id))
    else next.delete('open')
    setParams(next, { replace: true })
  }

  function setGame(id: string) {
    const next = new URLSearchParams(params)
    if (id) next.set('game', id)
    else next.delete('game')
    setParams(next, { replace: true })
  }

  function toggle(id: number) {
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]))
  }

  function stopPicking() {
    setPicking(false)
    setPicked([])
  }

  async function makeMontage() {
    setMaking(true)
    try {
      const clip = await desktop.montage(picked)
      toast({ title: 'Montage saved', sub: clip.title })
      stopPicking()
      clips.reload()
      setOpen(clip.id)
    } catch (e) {
      toast({ title: 'Montage failed', sub: (e as Error).message, tone: 'error' })
    } finally {
      setMaking(false)
    }
  }

  const pickedSeconds = picked.reduce((s, id) => s + (all.find((c) => c.id === id)?.duration ?? 0), 0)
  const stats = clips.data?.stats
  return (
    <div>
      <div className="page-head">
        <div>
          <div className="kicker bare">
            <b>02</b> Media hub
          </div>
          <h1>
            The <em>clips</em>
          </h1>
        </div>
        <div className="aside">
          {stats && (
            <span className="mono muted" style={{ fontSize: 12 }}>
              {stats.count} files · {fmtBytes(stats.bytes)} · {fmtHours(stats.seconds)} of footage
            </span>
          )}
          <button className="btn" aria-pressed={picking} onClick={() => (picking ? stopPicking() : setPicking(true))}>
            <FilmIcon /> {picking ? 'Cancel' : 'Montage'}
          </button>
          {status?.clips_dir && (
            <button
              className="btn"
              title={status.clips_dir}
              onClick={() => (shown[0] ? void desktop.reveal(shown[0].id) : toast({ title: 'No clips yet', sub: status.clips_dir }))}
            >
              <FolderIcon /> Folder
            </button>
          )}
        </div>
      </div>

      <div className="spread" style={{ marginBottom: 14 }}>
        <div className="chips-filter" role="group" aria-label="Type">
          {KINDS.map((k) => (
            <button key={k.id} className="fchip" aria-pressed={kind === k.id} onClick={() => setKind(k.id)}>
              {k.label}
              {k.id && stats?.by_kind[k.id] ? <span className="n">{stats.by_kind[k.id]}</span> : null}
            </button>
          ))}
          <button className="fchip" aria-pressed={favorites} onClick={() => setFavorites((f) => !f)}>
            <StarIcon filled={favorites} className="" /> Favorites
          </button>
        </div>
        <label className="field" style={{ width: 260 }}>
          <SearchIcon />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search titles" aria-label="Search clips" />
        </label>
      </div>
      {games.length > 1 && (
        <div className="chips-filter" style={{ marginBottom: 22 }} role="group" aria-label="Game">
          <button className="fchip" aria-pressed={!game} onClick={() => setGame('')}>
            All games
          </button>
          {games.map((g) => (
            <button key={g.id} className="fchip" aria-pressed={game === g.id} onClick={() => setGame(g.id)}>
              {g.name}
              <span className="n">{g.n}</span>
            </button>
          ))}
        </div>
      )}

      {clips.loading && !clips.data ? (
        <div className="clip-grid">
          {Array.from({ length: 8 }, (_, i) => (
            <div key={i} className="skeleton" style={{ aspectRatio: '16/9' }} />
          ))}
        </div>
      ) : shown.length === 0 ? (
        <div className="card empty">
          <span className="display">No clips yet</span>
          Hit <b className="mono">{settings?.hotkey_clip ?? 'F8'}</b> in game to save the last {settings?.buffer_seconds ?? 60} seconds,{' '}
          <b className="mono">{settings?.hotkey_record ?? 'F9'}</b> to record, <b className="mono">{settings?.hotkey_screenshot ?? 'F10'}</b> for a screenshot.
        </div>
      ) : (
        groupByDay(shown).map(([day, list]) => (
          <section key={day} style={{ marginBottom: 28 }}>
            <div className="kicker" style={{ marginBottom: 12 }}>
              {day} <span style={{ color: 'var(--text-2)' }}>{list.length}</span>
            </div>
            <div className="clip-grid">
              {list.map((c) => (
                <ClipTile
                  key={c.id}
                  clip={c}
                  order={picking ? picked.indexOf(c.id) + 1 || null : undefined}
                  onOpen={() => (picking ? c.kind !== 'screenshot' && toggle(c.id) : setOpen(c.id))}
                />
              ))}
            </div>
          </section>
        ))
      )}

      {picking && (
        <div className="pick-bar" role="toolbar" aria-label="Montage">
          <span className="n">{picked.length}</span>
          <div style={{ minWidth: 0 }}>
            <div className="t">{picked.length ? `${picked.length} clip${picked.length > 1 ? 's' : ''} · ${Math.round(pickedSeconds)}s` : 'Pick clips in the order they should play'}</div>
            <div className="s">Back to back at 1080p60, with a quick fade through black between clips</div>
          </div>
          <button className="btn primary" disabled={picked.length < 2 || making} onClick={() => void makeMontage()}>
            <FilmIcon /> {making ? 'Rendering…' : 'Make montage'}
          </button>
          <button className="icon-btn" onClick={stopPicking} aria-label="Cancel">
            <CloseIcon />
          </button>
        </div>
      )}

      {openIdx >= 0 && (
        <ClipViewer
          clip={shown[openIdx]}
          onClose={() => setOpen(null)}
          onPrev={openIdx > 0 ? () => setOpen(shown[openIdx - 1].id) : undefined}
          onNext={openIdx < shown.length - 1 ? () => setOpen(shown[openIdx + 1].id) : undefined}
          onChanged={(next) => {
            clips.reload()
            if (next === null) setOpen(shown[openIdx + 1]?.id ?? shown[openIdx - 1]?.id ?? null)
            else if (typeof next === 'number') setOpen(next)
          }}
        />
      )}
    </div>
  )
}
