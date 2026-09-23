import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../api'
import { ClipTile } from '../components/ClipTile'
import { EyeOffIcon, PlayIcon } from '../components/Icons'
import { useDesktop } from '../components/Shell'
import { desktop } from '../desktop'
import { fmtHours, hoursNumber, timeAgoUnix } from '../format'
import { useAsync, useGame } from '../hooks'
import { DesktopOnly } from './DesktopOnly'
import { useLaunch } from './Library'

export function GameDetail() {
  return (
    <DesktopOnly feature="Game pages">
      <GameDetailInner />
    </DesktopOnly>
  )
}

function GameDetailInner() {
  const { id = '' } = useParams()
  const data = useAsync(async () => {
    const [lib, sessions, clips, pt] = await Promise.all([desktop.library(true), desktop.gameSessions(id), desktop.clips({ game_id: id }), desktop.playtime(30)])
    return { game: lib.find((g) => g.id === id), sessions, clips: clips.items, daily: pt.daily }
  }, [id])
  const launch = useLaunch()
  const navigate = useNavigate()
  const [heroFailed, setHeroFailed] = useState(false)
  const [logoFailed, setLogoFailed] = useState(false)

  if (data.loading && !data.data) return <div className="skeleton" style={{ height: 380 }} />
  const game = data.data?.game
  if (!game) return <div className="card empty">That game isn’t in your library anymore.</div>
  const { sessions, clips, daily } = data.data!
  const longest = sessions.reduce((m, s) => Math.max(m, s.seconds), 0)
  const chart = daily.map((d) => ({ date: d.date.slice(5), hours: +((d.games[game.id] ?? 0) / 3600).toFixed(2) }))
  const heroSrc = !heroFailed ? game.art.hero : null

  return (
    <div>
      <section className="game-hero" style={{ '--tint': game.art.accent ?? '#333' } as React.CSSProperties}>
        {heroSrc ? <img className="bg" src={heroSrc} alt="" onError={() => setHeroFailed(true)} /> : <div className="fallback-bg" />}
        <div className="inner">
          <div>
            <div className="kicker bare" style={{ marginBottom: 14 }}>
              <b>//</b> {game.source} {game.running ? '· running now' : game.last_played ? `· last played ${timeAgoUnix(game.last_played)}` : ''}
            </div>
            {game.art.logo && !logoFailed ? (
              <img className="logo-img" src={game.art.logo} alt={game.name} onError={() => setLogoFailed(true)} />
            ) : (
              <h1>{game.name}</h1>
            )}
          </div>
          <div className="actions">
            <button className="btn big primary" onClick={() => void launch(game)} disabled={game.running}>
              <PlayIcon /> {game.running ? 'Running' : 'Play'}
            </button>
            <button
              className="icon-btn"
              title={game.hidden ? 'Show in library' : 'Hide from library'}
              onClick={async () => {
                await desktop.setHidden(game.id, !game.hidden)
                navigate('/library')
              }}
            >
              <EyeOffIcon />
            </button>
          </div>
        </div>
      </section>

      <div className="stat-row enter" style={{ marginBottom: 18 }}>
        <div className="stat hot">
          <div className="k">Total playtime</div>
          <div className="v">
            {hoursNumber(game.playtime)}
            <small>h</small>
          </div>
          <div className="d">tracked by Clutch</div>
        </div>
        <div className="stat">
          <div className="k">This week</div>
          <div className="v">{fmtHours(game.week)}</div>
        </div>
        <div className="stat">
          <div className="k">Sessions</div>
          <div className="v">{sessions.length}</div>
          <div className="d">longest {fmtHours(longest)}</div>
        </div>
        <div className="stat">
          <div className="k">Clips</div>
          <div className="v">{clips.length}</div>
        </div>
      </div>

      <div className="grid cols-3">
        <div className="stack">
          {game.stats_game && <LinkedStats statsGame={game.stats_game} />}
          <div className="card">
            <h2>Recent sessions</h2>
            {sessions.length === 0 ? (
              <p className="muted" style={{ margin: 0 }}>
                No sessions yet. Clutch records one whenever {game.name} runs for over a minute.
              </p>
            ) : (
              <div className="session-list">
                {sessions.slice(0, 8).map((s) => (
                  <div className="session" key={s.id} style={{ gridTemplateColumns: '1fr auto' }}>
                    <div className="when">
                      {new Date(s.started_at * 1000).toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}
                    </div>
                    <div className="len">{fmtHours(s.seconds)}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="stack">
          <div className="card">
            <h2>Last 30 days</h2>
            <div className="chart" style={{ height: 200 }}>
              <ResponsiveContainer>
                <BarChart data={chart} margin={{ top: 4, right: 4, left: -24, bottom: 0 }}>
                  <XAxis dataKey="date" tick={{ fill: '#6d6963', fontSize: 10, fontFamily: 'IBM Plex Mono' }} tickLine={false} axisLine={false} interval={4} />
                  <YAxis tick={{ fill: '#6d6963', fontSize: 10, fontFamily: 'IBM Plex Mono' }} tickLine={false} axisLine={false} />
                  <Tooltip
                    cursor={{ fill: 'rgba(255,255,255,.04)' }}
                    content={({ payload, label }) =>
                      payload?.length ? (
                        <div className="tt">
                          <div className="tt-title">{label}</div>
                          {fmtHours(Number(payload[0].value) * 3600)}
                        </div>
                      ) : null
                    }
                  />
                  <Bar dataKey="hours" fill="#d4ff3a" radius={0} maxBarSize={18} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
          <div className="card">
            <div className="spread" style={{ marginBottom: 12 }}>
              <h2 style={{ margin: 0 }}>Clips</h2>
              {clips.length > 0 && (
                <Link className="btn small" to={`/clips?game=${encodeURIComponent(game.id)}`}>
                  All {clips.length}
                </Link>
              )}
            </div>
            {clips.length === 0 ? (
              <p className="muted" style={{ margin: 0 }}>Nothing clipped yet. Press your clip hotkey in game to save the last few seconds.</p>
            ) : (
              <div className="clip-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}>
                {clips.slice(0, 6).map((c) => (
                  <ClipTile key={c.id} clip={c} onOpen={() => navigate(`/clips?open=${c.id}`)} />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

/** Connect this installed game to the player's stats profile (enables "my stats" and auto-sync). */
function LinkedStats({ statsGame }: { statsGame: string }) {
  const meta = useGame(statsGame)
  const { settings, setSettings, toast } = useDesktop()
  const linked = settings?.linked_profiles[statsGame]
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  if (!meta) return null

  async function link() {
    setBusy(true)
    try {
      const profile = await api.search(statsGame, q.trim())
      const next = await desktop.saveSettings({ linked_profiles: { ...(settings?.linked_profiles ?? {}), [statsGame]: profile.key } })
      setSettings(next)
      toast({ title: `Linked ${profile.name}`, sub: `${meta!.name} stats refresh after every session` })
    } catch (e) {
      toast({ title: 'Couldn’t find that player', sub: (e as Error).message, tone: 'error' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card" style={{ borderLeft: `3px solid ${meta.accent}` }}>
      <h2>Your {meta.name} stats</h2>
      {linked ? (
        <div className="row">
          <Link className="btn primary" to={`/${statsGame}/p/${encodeURIComponent(linked)}`}>
            Open my stats
          </Link>
          <span className="muted" style={{ fontSize: 12 }}>
            Auto-refreshes when you close the game.
          </span>
        </div>
      ) : (
        <form
          className="grid"
          style={{ gap: 8 }}
          onSubmit={(e) => {
            e.preventDefault()
            if (q.trim()) void link()
          }}
        >
          <p className="muted" style={{ margin: 0, fontSize: 12.5 }}>
            Link your account and Clutch pulls your new matches every time a session ends.
          </p>
          <div className="row">
            <label className="field" style={{ flex: 1 }}>
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={meta.search_hint} aria-label={`${meta.name} player`} />
            </label>
            <button className="btn primary" disabled={busy || !q.trim()}>
              {busy ? 'Linking…' : 'Link'}
            </button>
          </div>
        </form>
      )}
    </div>
  )
}
