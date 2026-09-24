import { useMemo, useState } from 'react'
import { GameTile, SOURCE_LABEL } from '../components/GameTile'
import { PlusIcon, RefreshIcon, SearchIcon } from '../components/Icons'
import { useDesktop } from '../components/Shell'
import { bridge, desktop, type LibraryGame } from '../desktop'
import { useAsync } from '../hooks'
import { DesktopOnly } from './DesktopOnly'

type Sort = 'recent' | 'played' | 'name'

export function useLaunch() {
  const { toast } = useDesktop()
  return async (g: LibraryGame) => {
    try {
      await desktop.launch(g.id)
      toast({ title: `Launching ${g.name}`, sub: `via ${SOURCE_LABEL[g.source] ?? g.source}` })
    } catch (e) {
      toast({ title: `Couldn't launch ${g.name}`, sub: (e as Error).message, tone: 'error' })
    }
  }
}

export function Library() {
  return (
    <DesktopOnly feature="Your game library and launcher">
      <LibraryInner />
    </DesktopOnly>
  )
}

function LibraryInner() {
  const lib = useAsync(() => desktop.library(), [])
  const [q, setQ] = useState('')
  const [source, setSource] = useState<string | null>(null)
  const [sort, setSort] = useState<Sort>('recent')
  const [scanning, setScanning] = useState(false)
  const launch = useLaunch()
  const { toast } = useDesktop()

  const games = lib.data ?? []
  const sources = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const g of games) counts[g.source] = (counts[g.source] ?? 0) + 1
    return Object.entries(counts).sort((a, b) => b[1] - a[1])
  }, [games])

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const list = games.filter((g) => (!source || g.source === source) && (!needle || g.name.toLowerCase().includes(needle)))
    const by: Record<Sort, (a: LibraryGame, b: LibraryGame) => number> = {
      recent: (a, b) => Number(b.running) - Number(a.running) || (b.last_played ?? 0) - (a.last_played ?? 0) || a.name.localeCompare(b.name),
      played: (a, b) => b.playtime - a.playtime || a.name.localeCompare(b.name),
      name: (a, b) => a.name.localeCompare(b.name),
    }
    return [...list].sort(by[sort])
  }, [games, q, source, sort])

  async function rescan() {
    setScanning(true)
    try {
      await desktop.scan()
      lib.reload()
    } finally {
      setScanning(false)
    }
  }

  async function addGame() {
    const exe = await bridge?.pickExecutable()
    if (!exe) return
    const name = exe.split(/[\\/]/).pop()?.replace(/\.(exe|lnk|url|bat)$/i, '') ?? 'Game'
    try {
      await desktop.addCustom(name, exe)
      lib.reload()
      toast({ title: `Added ${name}` })
    } catch (e) {
      toast({ title: 'Couldn’t add that file', sub: (e as Error).message, tone: 'error' })
    }
  }

  return (
    <div>
      <div className="page-head" data-echo="library">
        <div>
          <div className="kicker bare">
            <b>01</b> Library · {games.length} games · {sources.length} launchers
          </div>
          <h1>
            Your <em>games</em>
          </h1>
        </div>
        <div className="aside">
          {bridge && (
            <button className="btn" onClick={() => void addGame()}>
              <PlusIcon /> Add a game
            </button>
          )}
          <button className="btn" onClick={() => void rescan()} disabled={scanning}>
            <RefreshIcon /> {scanning ? 'Scanning…' : 'Rescan'}
          </button>
        </div>
      </div>

      <div className="spread" style={{ marginBottom: 20 }}>
        <div className="chips-filter" role="group" aria-label="Launcher">
          <button className="fchip" aria-pressed={source === null} onClick={() => setSource(null)}>
            All<span className="n">{games.length}</span>
          </button>
          {sources.map(([s, n]) => (
            <button key={s} className="fchip" aria-pressed={source === s} onClick={() => setSource(source === s ? null : s)}>
              {SOURCE_LABEL[s] ?? s}
              <span className="n">{n}</span>
            </button>
          ))}
        </div>
        <div className="row">
          <label className="field" style={{ width: 240 }}>
            <SearchIcon />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter games" aria-label="Filter games" />
          </label>
          <div className="seg" role="group" aria-label="Sort">
            {(['recent', 'played', 'name'] as Sort[]).map((s) => (
              <button key={s} aria-pressed={sort === s} onClick={() => setSort(s)}>
                {s === 'recent' ? 'Recent' : s === 'played' ? 'Most played' : 'A–Z'}
              </button>
            ))}
          </div>
        </div>
      </div>

      {lib.loading && !lib.data ? (
        <div className="library-grid">
          {Array.from({ length: 12 }, (_, i) => (
            <div key={i} className="skeleton" style={{ aspectRatio: '2/3' }} />
          ))}
        </div>
      ) : lib.error ? (
        <div className="card empty error">{lib.error.message}</div>
      ) : shown.length === 0 ? (
        <div className="card empty">
          <span className="display">Nothing here</span>
          {games.length ? 'No games match that filter.' : 'Clutch didn’t find any installed games. Try Rescan, or add one by hand.'}
        </div>
      ) : (
        <div className="library-grid">
          {shown.map((g) => (
            <GameTile key={g.id} game={g} onLaunch={(x) => void launch(x)} />
          ))}
        </div>
      )}
    </div>
  )
}
