import { Link } from 'react-router-dom'
import { desktop, type Report } from '../desktop'
import { deltaInfo, fmtHours, fmtValue, timeAgoUnix } from '../format'
import { useGames } from '../hooks'
import type { Fmt } from '../types'

/** A session's report card: time, clips, and (with a linked stats account) record, form and best game. */
export function ReportCard({ report, compact = false }: { report: Report; compact?: boolean }) {
  const games = useGames()
  const st = report.stats
  const meta = games.find((g) => g.id === report.stats_game)
  const m = st?.metric
  const delta = m && m.session !== null && m.usual !== null ? m.session - m.usual : null
  const tone = delta !== null ? deltaInfo(delta, (m && meta?.metrics[m.key]?.higher_is_better) ?? true) : null
  const record = st ? (st.wins > st.losses ? 'up' : st.wins < st.losses ? 'down' : 'flat') : null
  const date = new Date(report.started_at * 1000)
  const shown = compact ? 3 : 5
  return (
    <article className={`report ${compact ? 'compact' : ''}`} style={{ '--game': meta?.accent ?? 'var(--volt)' } as React.CSSProperties}>
      <header>
        <img className="gicon" src={desktop.iconUrl(report.game_id)} alt="" onError={(e) => (e.currentTarget.style.display = 'none')} />
        <div style={{ minWidth: 0 }}>
          <div className="name">{report.game_name}</div>
          <div className="when">
            {date.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })} ·{' '}
            {date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })} · {timeAgoUnix(report.ended_at)}
          </div>
        </div>
        {st && (
          <div className={`grade ${record}`} title="Wins – losses this session">
            {st.wins}–{st.losses}
          </div>
        )}
      </header>

      <div className="report-stats">
        <div>
          <div className="k">Played</div>
          <div className="v">{fmtHours(report.seconds)}</div>
        </div>
        <div>
          <div className="k">Clips</div>
          <div className="v">{report.clips.length}</div>
        </div>
        {st ? (
          <>
            <div>
              <div className="k">Games</div>
              <div className="v">{st.games}</div>
            </div>
            {m && (
              <div>
                <div className="k">{m.label}</div>
                <div className="v">
                  {fmtValue(m.session, m.fmt as Fmt)}
                  {tone && (
                    <span className={`delta ${tone.tone}`} title={`Usually ${fmtValue(m.usual, m.fmt as Fmt)}`}>
                      {tone.text}
                    </span>
                  )}
                </div>
              </div>
            )}
          </>
        ) : null}
      </div>

      {st?.best && !compact && (
        <div className="report-best">
          <span className="kicker bare">
            <b>MVP</b> game
          </span>
          <span>
            {st.best.character} · {st.best.result === 'win' ? 'Win' : st.best.result === 'loss' ? 'Loss' : st.best.result}
            {st.best.score_line ? ` · ${st.best.score_line}` : ''} · {fmtValue(st.best.value, m?.fmt as Fmt)} {m?.label}
          </span>
        </div>
      )}
      {st?.rank && !compact && <div className="report-rank mono">Rank · {st.rank}</div>}

      {report.clips.length > 0 && (
        <div className="report-clips">
          {report.clips.slice(0, shown).map((id) => (
            <Link key={id} to={`/clips?open=${id}`} className="rthumb" aria-label="Open clip">
              <img src={desktop.clipThumb(id)} alt="" loading="lazy" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} />
            </Link>
          ))}
          {report.clips.length > shown && <span className="more mono">+{report.clips.length - shown}</span>}
        </div>
      )}
      {!st && !compact && (
        <p className="hint" style={{ margin: 0 }}>
          {report.stats_game ? 'No ranked matches in this window.' : 'Link this game’s stats account to get W/L, form and your best game here.'}
        </p>
      )}
    </article>
  )
}
