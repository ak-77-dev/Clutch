import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { desktop, useDesktopEvents, type PlaytimeDay } from '../desktop'
import { fmtHours, hoursNumber } from '../format'
import { useAsync } from '../hooks'
import { DesktopOnly } from './DesktopOnly'

export function Playtime() {
  return (
    <DesktopOnly feature="Playtime tracking">
      <PlaytimeInner />
    </DesktopOnly>
  )
}

/** Longest run of consecutive days with any play, and the run that includes today/yesterday. */
export function streaks(days: PlaytimeDay[]): { current: number; best: number } {
  let best = 0
  let run = 0
  for (const d of days) {
    run = d.seconds >= 60 ? run + 1 : 0
    best = Math.max(best, run)
  }
  let current = 0
  for (let i = days.length - 1; i >= 0; i--) {
    if (days[i].seconds >= 60) current++
    else if (i === days.length - 1) continue // today not played yet doesn't break the streak
    else break
  }
  return { current, best }
}

function level(seconds: number, max: number): number {
  if (seconds < 60) return 0
  const f = seconds / Math.max(max, 1)
  return f > 0.75 ? 4 : f > 0.5 ? 3 : f > 0.25 ? 2 : 1
}

function PlaytimeInner() {
  const data = useAsync(() => Promise.all([desktop.playtime(182), desktop.library(true)]), [])
  useDesktopEvents((e) => {
    if (e.type === 'session_end' || e.type === 'game_started') data.reload()
  })

  const view = useMemo(() => {
    if (!data.data) return null
    const [pt, lib] = data.data
    const byId = new Map(lib.map((g) => [g.id, g]))
    const total = pt.games.reduce((s, g) => s + g.seconds, 0)
    const week = pt.daily.slice(-7).reduce((s, d) => s + d.seconds, 0)
    const lastWeek = pt.daily.slice(-14, -7).reduce((s, d) => s + d.seconds, 0)
    const max = Math.max(...pt.daily.map((d) => d.seconds), 1)
    // pad the front so the heatmap's first column starts on a Sunday
    const first = new Date(pt.daily[0]?.date ?? Date.now())
    const pad = first.getUTCDay()
    return { pt, byId, total, week, lastWeek, max, pad, ...streaks(pt.daily) }
  }, [data.data])

  if (!view) return data.error ? <div className="card empty error">{data.error.message}</div> : <div className="skeleton" style={{ height: 400 }} />
  const { pt, byId, total, week, lastWeek, max, pad, current, best } = view
  const top = pt.games[0]?.seconds ?? 1
  const delta = lastWeek ? Math.round(((week - lastWeek) / lastWeek) * 100) : null

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="kicker bare">
            <b>03</b> Playtime · tracked automatically
          </div>
          <h1>
            Time <em>played</em>
          </h1>
        </div>
      </div>

      <div className="stat-row enter" style={{ marginBottom: 18 }}>
        <div className="stat hot">
          <div className="k">All time</div>
          <div className="v">
            {hoursNumber(total)}
            <small>h</small>
          </div>
          <div className="d">{pt.games.length} games</div>
        </div>
        <div className="stat">
          <div className="k">Last 7 days</div>
          <div className="v">{fmtHours(week)}</div>
          <div className={`d ${delta === null ? '' : delta >= 0 ? 'up' : 'down'}`}>{delta === null ? '—' : `${delta >= 0 ? '▲' : '▼'} ${Math.abs(delta)}% vs week before`}</div>
        </div>
        <div className="stat">
          <div className="k">Streak</div>
          <div className="v">
            {current}
            <small>d</small>
          </div>
          <div className="d">best {best} days</div>
        </div>
        <div className="stat">
          <div className="k">Now</div>
          <div className="v" style={{ fontSize: pt.now.length ? 30 : 44 }}>
            {pt.now.length ? pt.now[0].game_name : '—'}
          </div>
          <div className="d">{pt.now.length ? `for ${fmtHours(pt.now[0].seconds)}` : 'nothing running'}</div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="spread" style={{ marginBottom: 14 }}>
          <h2 style={{ margin: 0 }}>Last 26 weeks</h2>
          <div className="legend-scale">
            less <i style={{ background: 'var(--panel-2)' }} />
            <i style={{ background: 'rgba(212,255,58,.22)' }} />
            <i style={{ background: 'rgba(212,255,58,.45)' }} />
            <i style={{ background: 'rgba(212,255,58,.7)' }} />
            <i style={{ background: 'var(--volt)' }} /> more
          </div>
        </div>
        <div className="heatmap" role="img" aria-label="Daily playtime heatmap">
          {Array.from({ length: pad }, (_, i) => (
            <i key={`pad${i}`} className="future" />
          ))}
          {pt.daily.map((d) => (
            <i key={d.date} data-l={level(d.seconds, max)} title={`${new Date(d.date + 'T12:00').toDateString()} · ${fmtHours(d.seconds)}`} />
          ))}
        </div>
      </div>

      <div className="grid cols-2">
        <div className="card">
          <h2>By game</h2>
          {pt.games.length === 0 ? (
            <p className="muted" style={{ margin: 0 }}>
              Nothing tracked yet. Leave Clutch running in the tray and it logs every session automatically.
            </p>
          ) : (
            <div className="bars">
              {pt.games.map((g) => {
                const lib = byId.get(g.game_id)
                return (
                  <Link key={g.game_id} to={`/library/${encodeURIComponent(g.game_id)}`} className="bar-row">
                    <img className="gicon" src={desktop.iconUrl(g.game_id)} alt="" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} />
                    <div style={{ minWidth: 0 }}>
                      <div className="name">{lib?.name ?? g.game_name}</div>
                      <div className="track">
                        <i style={{ width: `${(g.seconds / top) * 100}%`, background: lib?.art.accent ?? undefined }} />
                      </div>
                    </div>
                    <div className="val">
                      {fmtHours(g.seconds)}
                      <small>{g.sessions} sessions</small>
                    </div>
                  </Link>
                )
              })}
            </div>
          )}
        </div>
        <div className="card">
          <h2>Recent sessions</h2>
          {pt.sessions.length === 0 ? (
            <p className="muted" style={{ margin: 0 }}>No sessions yet.</p>
          ) : (
            <div className="session-list">
              {pt.sessions.slice(0, 12).map((s) => (
                <div className="session" key={s.id}>
                  <img className="gicon" src={desktop.iconUrl(s.game_id)} alt="" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} />
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 600 }}>{s.game_name}</div>
                    <div className="when">
                      {new Date(s.started_at * 1000).toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}
                      {s.active ? ' · live' : ''}
                    </div>
                  </div>
                  <div className="len">{fmtHours(s.seconds)}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
