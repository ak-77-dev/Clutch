import type { Fmt, Metric } from './types'

export function fmtValue(v: number | null | undefined, fmt: Fmt = 'float1'): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  switch (fmt) {
    case 'int':
      return Math.round(v).toLocaleString()
    case 'float2':
      return v.toFixed(2)
    case 'pct':
      return `${v.toFixed(v < 10 ? 1 : 0)}%`
    case 'time':
      return fmtDuration(v)
    default:
      return v.toFixed(1)
  }
}

export function fmtMetric(metric: Metric | undefined, v: number | null | undefined): string {
  return fmtValue(v, metric?.fmt ?? 'float1')
}

export function fmtDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

export function fmtWinRate(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : `${Math.round(v)}%`
}

/** "3h ago", "2d ago", or a short date beyond a week. */
export function timeAgo(iso: string, now: Date = new Date()): string {
  const then = new Date(iso)
  const diff = (now.getTime() - then.getTime()) / 1000
  if (Number.isNaN(diff)) return ''
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86_400) return `${Math.floor(diff / 3600)}h ago`
  if (diff < 7 * 86_400) return `${Math.floor(diff / 86_400)}d ago`
  return then.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** Signed delta with the right sense of "better" for the metric. */
export function deltaInfo(delta: number | null | undefined, higherIsBetter = true): { text: string; tone: 'up' | 'down' | 'flat' } {
  if (delta === null || delta === undefined || Math.abs(delta) < 1e-9) return { text: 'no change', tone: 'flat' }
  const good = higherIsBetter ? delta > 0 : delta < 0
  const mag = Math.abs(delta)
  const text = `${delta > 0 ? '▲' : '▼'} ${mag >= 10 ? mag.toFixed(0) : mag.toFixed(mag >= 1 ? 1 : 2)}`
  return { text, tone: good ? 'up' : 'down' }
}

export const RESULT_LABEL = { win: 'Victory', loss: 'Defeat', draw: 'Draw', remake: 'Remake' } as const
