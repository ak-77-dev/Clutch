import { createContext, useContext, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { fmtDuration, fmtMetric, RESULT_LABEL, timeAgo } from '../format'
import type { Game, MatchDetail, MatchSummary, ScoreRow } from '../types'
import { ClipIcon } from './Icons'
import { Portrait } from './Portrait'

/** match id -> ids of clips recorded during it (desktop app, linked account only). */
export const MatchClipsContext = createContext<Record<string, number[]>>({})

/** The big number on a match row: K/D/A, Rocket League's G/A/Sv, or the game's lead stat (chess rating, TFT place...). */
function headlineKey(game: Game, m: MatchSummary): string | null {
  if (m.metrics.kills !== undefined && m.metrics.deaths !== undefined) return null
  if (m.metrics.goals !== undefined) return null
  return game.card_metrics[0] ?? null
}

function Kda({ m, game }: { m: MatchSummary; game: Game }) {
  const key = headlineKey(game, m)
  if (key) {
    const metric = game.metrics[key]
    return (
      <div className="kda">
        {fmtMetric(metric, m.metrics[key])}
        <span className="slash"> {metric.short ?? metric.label}</span>
      </div>
    )
  }
  if (m.metrics.kills === undefined) {
    // Rocket League: goals / assists / saves instead of K/D/A
    return (
      <div className="kda">
        {m.metrics.goals}
        <span className="slash">G</span> {m.metrics.assists}
        <span className="slash">A</span> {m.metrics.saves}
        <span className="slash">Sv</span>
      </div>
    )
  }
  return (
    <div className="kda">
      {m.metrics.kills}
      <span className="slash">/</span>
      <span className="deaths">{m.metrics.deaths}</span>
      <span className="slash">/</span>
      {m.metrics.assists}
    </div>
  )
}

export function MatchCard({ game, match, playerKey }: { game: Game; match: MatchSummary; playerKey: string }) {
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState<MatchDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const clipIds = useContext(MatchClipsContext)[match.id]

  async function toggle() {
    setOpen((o) => !o)
    if (!detail && !open) {
      try {
        setDetail(await api.match(game.id, playerKey, match.id))
      } catch (e) {
        setError((e as Error).message)
      }
    }
  }

  const headline = headlineKey(game, match)
  const statKeys = game.card_metrics.filter((k) => k !== headline && !['kills', 'deaths', 'assists', 'goals', 'saves'].includes(k))
  return (
    <article className={`match ${match.result}`}>
      {clipIds?.length ? (
        <Link className="match-clips" to={`/clips?open=${clipIds[0]}`} title={`${clipIds.length} clip${clipIds.length > 1 ? 's' : ''} from this match`}>
          <ClipIcon /> {clipIds.length}
        </Link>
      ) : null}
      <button className="match-row" onClick={() => void toggle()} aria-expanded={open}>
        <div>
          <div className="res">{RESULT_LABEL[match.result]}</div>
          <div className="meta">
            {match.mode}
            <br />
            {timeAgo(match.date)} · {fmtDuration(match.duration_s)}
          </div>
        </div>
        <Portrait src={match.character_icon} name={match.character} />
        <div>
          <Kda m={match} game={game} />
          <div className="meta">
            {match.character}
            {match.role ? ` · ${match.role}` : ''}
            {match.map ? ` · ${match.map}` : ''}
            {match.score_line ? ` · ${match.score_line}` : ''}
          </div>
        </div>
        <div className="mstats-col">
          <div className="mstats">
            {statKeys.map((k) => (
              <span key={k}>
                {game.metrics[k].short ?? game.metrics[k].label} <b>{fmtMetric(game.metrics[k], match.metrics[k])}</b>
              </span>
            ))}
            {match.rank_label && <span>{match.rank_label}</span>}
          </div>
          {match.items.length > 0 && (
            <div className="items">
              {match.items.map((src, i) => (
                <img key={i} src={src} alt="" loading="lazy" />
              ))}
            </div>
          )}
        </div>
        <span className={`chev${open ? ' open' : ''}`} aria-hidden>
          ›
        </span>
      </button>
      {open && (
        <div className="match-detail">
          {error ? <p className="error">{error}</p> : detail ? <Scoreboard rows={detail.scoreboard} link={detail.link} /> : <div className="skeleton" style={{ height: 160 }} />}
        </div>
      )}
    </article>
  )
}

export function Scoreboard({ rows, link }: { rows: ScoreRow[]; link: string | null }) {
  const teams = [...new Set(rows.map((r) => r.team))]
  const statCols = Object.keys(rows[0]?.stats ?? {})
  const hasItems = rows.some((r) => r.extra?.items?.length)
  const hasRank = rows.some((r) => r.rank)
  return (
    <div>
      {teams.map((t) => {
        const teamRows = rows.filter((r) => r.team === t)
        const known = teamRows.some((r) => r.extra?.won !== undefined)
        const won = teamRows.some((r) => r.extra?.won)
        const mine = teamRows.some((r) => r.is_self)
        return (
          <div key={t} className="table-wrap">
            {/* Team names stay neutral: blue/red in this UI mean win/loss, not the side you played on. */}
            <div className="team-label">
              {t} team{mine ? ' (you)' : ''}
              {known && <span style={{ color: won ? 'var(--win)' : 'var(--loss)' }}> · {won ? 'Victory' : 'Defeat'}</span>}
            </div>
            <table>
              <thead>
                <tr>
                  <th>Player</th>
                  {hasRank && <th>Rank</th>}
                  {statCols.map((c) => (
                    <th key={c} className="num">
                      {c}
                    </th>
                  ))}
                  {hasItems && <th>Items</th>}
                </tr>
              </thead>
              <tbody>
                {teamRows.map((r) => (
                  <tr key={r.name} className={r.is_self ? 'self' : undefined}>
                    <td>
                      <div className="cell-char">
                        <Portrait src={r.character_icon} name={r.character} size="xs" />
                        <span>
                          {r.name}
                          {r.extra?.mvp ? ' ★' : ''}
                          <span className="muted" style={{ fontSize: 11 }}>
                            {' '}
                            {r.character}
                          </span>
                        </span>
                      </div>
                    </td>
                    {hasRank && <td className="muted">{r.rank ?? '—'}</td>}
                    {statCols.map((c) => (
                      <td key={c} className="num">
                        {typeof r.stats[c] === 'number' ? (r.stats[c] as number).toLocaleString() : r.stats[c]}
                      </td>
                    ))}
                    {hasItems && (
                      <td>
                        <div className="items" style={{ marginTop: 0 }}>
                          {(r.extra.items ?? []).map((src, i) => (
                            <img key={i} src={src} alt="" loading="lazy" />
                          ))}
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      })}
      {link && (
        <p style={{ margin: '10px 0 0', fontSize: 12 }}>
          <a href={link} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--s1)' }}>
            Open match ↗
          </a>
        </p>
      )}
    </div>
  )
}
