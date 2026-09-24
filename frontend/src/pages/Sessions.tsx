import { useMemo } from 'react'
import { ReportCard } from '../components/ReportCard'
import { desktop, useDesktopEvents } from '../desktop'
import { fmtHours } from '../format'
import { useAsync } from '../hooks'
import { DesktopOnly } from './DesktopOnly'

export function Sessions() {
  return (
    <DesktopOnly feature="Session report cards">
      <SessionsInner />
    </DesktopOnly>
  )
}

function SessionsInner() {
  const reports = useAsync(() => desktop.reports(60), [])
  useDesktopEvents((e) => {
    if (e.type === 'report_ready' || e.type === 'session_end') reports.reload()
  })
  const list = useMemo(() => reports.data ?? [], [reports.data])
  const week = useMemo(() => {
    const since = Date.now() / 1000 - 7 * 86400
    const recent = list.filter((r) => r.ended_at >= since)
    const w = recent.reduce((s, r) => s + (r.stats?.wins ?? 0), 0)
    const l = recent.reduce((s, r) => s + (r.stats?.losses ?? 0), 0)
    return {
      sessions: recent.length,
      seconds: recent.reduce((s, r) => s + r.seconds, 0),
      clips: recent.reduce((s, r) => s + r.clips.length, 0),
      record: w + l ? `${w}–${l}` : '—',
      longest: recent.reduce((best, r) => Math.max(best, r.seconds), 0),
    }
  }, [list])

  return (
    <div>
      <div className="page-head">
        <div>
          <div className="kicker bare">
            <b>04</b> Sessions
          </div>
          <h1>
            Report <em>cards</em>
          </h1>
        </div>
        <div className="aside">
          <span className="mono muted" style={{ fontSize: 12, maxWidth: 380, textAlign: 'right' }}>
            Built when you close a game. Linked accounts add W/L, your form vs. usual and the MVP game once the matches sync.
          </span>
        </div>
      </div>

      <div className="stat-row enter" style={{ marginBottom: 26 }}>
        <div className="stat hot">
          <div className="k">Sessions · 7 days</div>
          <div className="v">{week.sessions}</div>
        </div>
        <div className="stat">
          <div className="k">Time</div>
          <div className="v" style={{ fontSize: 34 }}>
            {fmtHours(week.seconds)}
          </div>
        </div>
        <div className="stat">
          <div className="k">Record</div>
          <div className="v">{week.record}</div>
        </div>
        <div className="stat">
          <div className="k">Clips</div>
          <div className="v">{week.clips}</div>
          <div className="d">longest session {fmtHours(week.longest)}</div>
        </div>
      </div>

      {reports.loading && !reports.data ? (
        <div className="report-grid">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="skeleton" style={{ height: 210 }} />
          ))}
        </div>
      ) : list.length === 0 ? (
        <div className="card empty">
          <span className="display">No sessions yet</span>
          Play something from your library. When you close the game, its report card lands here.
        </div>
      ) : (
        <div className="report-grid">
          {list.map((r) => (
            <ReportCard key={r.id} report={r} />
          ))}
        </div>
      )}
    </div>
  )
}
