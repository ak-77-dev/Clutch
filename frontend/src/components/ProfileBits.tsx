import { deltaInfo, fmtMetric, fmtWinRate } from '../format'
import type { Game, GroupRow, Insight, Overview, RankEntry } from '../types'
import { Portrait } from './Portrait'

const TIER_COLOR: Record<string, string> = {
  iron: '#7a6f6a', bronze: '#b0714a', silver: '#a6b0b9', gold: '#e0b24a', platinum: '#45c1b1', emerald: '#35b673',
  diamond: '#7a97ff', master: '#b86ae0', grandmaster: '#e0525a', challenger: '#f5cc59', ascendant: '#3fbf86',
  immortal: '#d8466b', radiant: '#f7e27c', champion: '#a867e0', grand: '#e0525a', supersonic: '#ffffff',
}

export function RankCard({ ranks }: { ranks: RankEntry[] }) {
  if (!ranks.length) return <p className="muted">Unranked</p>
  return (
    <div>
      {ranks.map((r) => {
        const tier = (r.tier || r.label || '').toLowerCase().split(' ')[0]
        const games = (r.wins ?? 0) + (r.losses ?? 0)
        return (
          <div className="rank" key={r.queue}>
            <div className="emblem" style={{ background: r.icon ? 'transparent' : TIER_COLOR[tier] ?? '#3a4050' }}>
              {r.icon ? <img src={r.icon} alt="" /> : (r.label ?? '?').slice(0, 1)}
            </div>
            <div>
              <div className="queue">{r.queue}</div>
              <div className="label">
                {r.label ?? 'Unranked'}
                {r.lp != null && <span className="muted" style={{ fontSize: 13, fontWeight: 500 }}> · {r.lp} {r.queue === 'Competitive' ? 'RR' : 'LP'}</span>}
              </div>
              {games > 0 && (
                <div className="rec">
                  {r.wins}W {r.losses}L · {Math.round((100 * (r.wins ?? 0)) / games)}%
                </div>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export function StatTiles({ game, ov }: { game: Game; ov: Overview }) {
  const s = ov.summary
  const cmp = ov.compare
  const wr = deltaInfo(cmp?.stats.win_rate?.delta)
  return (
    <div className="tiles">
      <div className="tile">
        <div className="k">Win rate</div>
        <div className="v">{fmtWinRate(s.win_rate)}</div>
        <div className={`d ${wr.tone}`}>
          {s.wins}W {s.losses}L{cmp ? ` · ${wr.text} pts` : ''}
        </div>
      </div>
      {game.kpis.map((k) => {
        const m = game.metrics[k]
        const d = deltaInfo(cmp?.stats[k]?.delta, m.higher_is_better)
        return (
          <div className="tile" key={k}>
            <div className="k">{m.label}</div>
            <div className="v">{fmtMetric(m, s.avg[k])}</div>
            <div className={`d ${d.tone}`} title={cmp ? `last ${cmp.window} vs previous ${cmp.window} games` : undefined}>
              {cmp ? `${d.text} recent` : '—'}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export function CharacterTable({ game, rows, limit, onPick }: { game: Game; rows: GroupRow[]; limit?: number; onPick?: (name: string) => void }) {
  const shown = limit ? rows.slice(0, limit) : rows
  if (!shown.length) return <p className="muted">No games yet.</p>
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{game.character_label}</th>
            <th className="num">Games</th>
            <th>Win rate</th>
            {game.card_metrics.map((k) => (
              <th key={k} className="num">
                {game.metrics[k].short ?? game.metrics[k].label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((r) => (
            <tr key={r.name} className={onPick ? 'clickable' : undefined} onClick={onPick ? () => onPick(r.name) : undefined}>
              <td>
                <div className="cell-char">
                  <Portrait src={r.icon} name={r.name} size="xs" />
                  {r.name}
                </div>
              </td>
              <td className="num">{r.games}</td>
              <td>
                <span className="wr-bar">
                  <span className="bar" aria-hidden>
                    <i style={{ width: `${r.win_rate ?? 0}%` }} />
                  </span>
                  {fmtWinRate(r.win_rate)}
                </span>
              </td>
              {game.card_metrics.map((k) => (
                <td key={k} className="num">
                  {fmtMetric(game.metrics[k], r.avg[k])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const TONE_LABEL = { positive: 'Up', negative: 'Down', tip: 'Tip', neutral: 'Note' }

export function Insights({ items }: { items: Insight[] }) {
  return (
    <ul className="insights">
      {items.map((i) => (
        <li key={i.text}>
          <span className={`tone ${i.tone}`}>{TONE_LABEL[i.tone]}</span>
          <span>{i.text}</span>
        </li>
      ))}
    </ul>
  )
}

export function SimpleGroupTable({ label, rows }: { label: string; rows: { name: string; games: number; win_rate: number | null }[] }) {
  if (!rows.length) return null
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{label}</th>
            <th className="num">Games</th>
            <th>Win rate</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name}>
              <td>{r.name}</td>
              <td className="num">{r.games}</td>
              <td>
                <span className="wr-bar">
                  <span className="bar" aria-hidden>
                    <i style={{ width: `${r.win_rate ?? 0}%` }} />
                  </span>
                  {fmtWinRate(r.win_rate)}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
