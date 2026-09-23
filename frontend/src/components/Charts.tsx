import { useState } from 'react'
import {
  Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { fmtMetric, fmtWinRate, shortDate } from '../format'
import type { Game, Overview } from '../types'

const AXIS = { stroke: '#6f778a', fontSize: 11, tickLine: false, axisLine: false } as const
const GRID = <CartesianGrid stroke="rgba(255,255,255,0.06)" vertical={false} />

function Tip({ active, payload, title, lines }: {
  active?: boolean
  payload?: { payload: Record<string, unknown> }[]
  title: (p: Record<string, unknown>) => string
  lines: (p: Record<string, unknown>) => string[]
}) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload
  return (
    <div className="tt">
      <div className="tt-title">{title(p)}</div>
      {lines(p).map((l) => (
        <div key={l}>{l}</div>
      ))}
    </div>
  )
}

function trendRows(ov: Overview, key: string) {
  const values = ov.trend[key] as (number | null)[]
  return ov.trend.dates.map((d, i) => ({ i: i + 1, date: d, v: values[i] }))
}

export function FormChart({ ov }: { ov: Overview }) {
  const rows = trendRows(ov, 'win_rate')
  return (
    <div className="chart" role="img" aria-label={`Rolling ${ov.trend.window}-game win rate`}>
      <ResponsiveContainer>
        <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
          {GRID}
          <XAxis dataKey="i" {...AXIS} minTickGap={40} tickFormatter={(i) => `#${i}`} />
          <YAxis {...AXIS} domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} tickFormatter={(v) => `${v}%`} />
          <ReferenceLine y={50} stroke="#3a4050" strokeDasharray="4 4" />
          <Tooltip cursor={{ stroke: '#3a4050' }} content={<Tip title={(p) => `Game ${p.i} · ${shortDate(p.date as string)}`} lines={(p) => [`Win rate ${fmtWinRate(p.v as number)}`]} />} />
          <Line dataKey="v" stroke="var(--s1)" strokeWidth={2} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

export function TrendChart({ game, ov }: { game: Game; ov: Overview }) {
  const [key, setKey] = useState(game.trend_metrics[0])
  const metric = game.metrics[key]
  const rows = trendRows(ov, key)
  return (
    <div>
      <div className="chart-head">
        <span className="muted" style={{ fontSize: 12 }}>
          Rolling {ov.trend.window}-game average
        </span>
        <div className="seg" role="group" aria-label="Metric">
          {game.trend_metrics.map((k) => (
            <button key={k} aria-pressed={k === key} onClick={() => setKey(k)}>
              {game.metrics[k].short ?? game.metrics[k].label}
            </button>
          ))}
        </div>
      </div>
      <div className="chart" role="img" aria-label={`Rolling ${metric.label}`}>
        <ResponsiveContainer>
          <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
            {GRID}
            <XAxis dataKey="i" {...AXIS} minTickGap={40} tickFormatter={(i) => `#${i}`} />
            <YAxis {...AXIS} domain={['auto', 'auto']} tickFormatter={(v) => fmtMetric(metric, v)} />
            <Tooltip cursor={{ stroke: '#3a4050' }} content={<Tip title={(p) => `Game ${p.i} · ${shortDate(p.date as string)}`} lines={(p) => [`${metric.label} ${fmtMetric(metric, p.v as number)}`]} />} />
            <Line dataKey="v" stroke="var(--s1)" strokeWidth={2} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

export function TiltChart({ ov }: { ov: Overview }) {
  const rows = ov.sessions.tilt_curve
  return (
    <div className="chart" role="img" aria-label="Win rate by game number within a session">
      <ResponsiveContainer>
        <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
          {GRID}
          <XAxis dataKey="game" {...AXIS} tickFormatter={(g) => `Game ${g}`} />
          <YAxis {...AXIS} domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} tickFormatter={(v) => `${v}%`} />
          <ReferenceLine y={50} stroke="#3a4050" strokeDasharray="4 4" />
          <Tooltip cursor={{ fill: 'rgba(255,255,255,0.04)' }} content={<Tip title={(p) => `Game ${p.game} of a session`} lines={(p) => [`${fmtWinRate(p.win_rate as number)} win rate`, `${p.games} games`]} />} />
          <Bar dataKey="win_rate" fill="var(--s1)" radius={[4, 4, 0, 0]} maxBarSize={40} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

export function FactorsChart({ game, ov }: { game: Game; ov: Overview }) {
  const rows = ov.win_factors.map((f) => {
    const m = game.metrics[f.metric]
    const higherHelps = f.difference > 0
    return {
      label: `${higherHelps ? 'Higher' : 'Lower'} ${f.label}`,
      lift: Math.abs(f.difference),
      withIt: higherHelps ? f.high_win_rate : f.low_win_rate,
      without: higherHelps ? f.low_win_rate : f.high_win_rate,
      split: fmtMetric(m, f.median),
    }
  })
  if (!rows.length) return <p className="muted">Needs 16+ games.</p>
  return (
    <div style={{ height: 40 + rows.length * 34 }} role="img" aria-label="Win-rate lift by habit">
      <ResponsiveContainer>
        <BarChart data={rows} layout="vertical" margin={{ top: 0, right: 16, bottom: 0, left: 8 }}>
          <CartesianGrid stroke="rgba(255,255,255,0.06)" horizontal={false} />
          <XAxis type="number" {...AXIS} tickFormatter={(v) => `+${v}`} />
          <YAxis type="category" dataKey="label" {...AXIS} width={170} tick={{ fill: '#aab1c1', fontSize: 12 }} />
          <Tooltip cursor={{ fill: 'rgba(255,255,255,0.04)' }} content={<Tip title={(p) => p.label as string} lines={(p) => [`+${Math.round(p.lift as number)} pts win rate`, `${fmtWinRate(p.withIt as number)} vs ${fmtWinRate(p.without as number)} (split at ${p.split})`]} />} />
          <Bar dataKey="lift" fill="var(--s1)" radius={[0, 4, 4, 0]} maxBarSize={20} isAnimationActive={false}>
            {rows.map((r) => (
              <Cell key={r.label} fill={r.lift >= 10 ? 'var(--s1)' : '#34507a'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

export function RankChart({ ov }: { ov: Overview }) {
  const pts = ov.rank_history.map((p) => ({ ...p, t: Date.parse(p.date) }))
  if (pts.length < 2) return null
  const labels = new Map<number, string>()
  for (const p of pts) if (p.label && !labels.has(Math.floor(p.value))) labels.set(Math.floor(p.value), p.label.replace(/ Div \d$/, ''))
  const values = pts.map((p) => p.value)
  const lo = Math.floor(Math.min(...values))
  const hi = Math.ceil(Math.max(...values) + 0.01)
  return (
    <div className="chart" role="img" aria-label="Rank over time">
      <ResponsiveContainer>
        <LineChart data={pts} margin={{ top: 8, right: 8, bottom: 0, left: 24 }}>
          {GRID}
          <XAxis dataKey="t" type="number" domain={['dataMin', 'dataMax']} {...AXIS} tickFormatter={(t) => shortDate(new Date(t).toISOString())} minTickGap={50} />
          <YAxis {...AXIS} domain={[lo, hi]} allowDecimals={false} tickCount={hi - lo + 1} tickFormatter={(v) => labels.get(v) ?? ''} width={70} />
          <Tooltip cursor={{ stroke: '#3a4050' }} content={<Tip title={(p) => shortDate(p.date as string)} lines={(p) => [String(p.label ?? '')]} />} />
          <Line dataKey="value" type="stepAfter" stroke="var(--s1)" strokeWidth={2} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
