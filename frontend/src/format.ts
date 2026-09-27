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

/** "Champion" -> "Champions", "Hero" -> "Heroes". */
export function plural(noun: string): string {
  return /[^aeiou]o$/i.test(noun) ? `${noun}es` : `${noun}s`
}

export function fmtDuration(seconds: number): string {
  const total = Math.round(seconds) // round first: 119.6 s is "2:00", not "1:60"
  const m = Math.floor(total / 60)
  return `${m}:${String(total % 60).padStart(2, '0')}`
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

/** 3725 -> "1h 2m"; 42 -> "42s"; 0 -> "0m". */
export function fmtHours(seconds: number | null | undefined): string {
  const s = Math.max(0, Math.round(seconds ?? 0))
  if (s < 60) return s ? `${s}s` : '0m'
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return h ? `${h}h ${m}m` : `${m}m`
}

/** Total hours with one decimal under 100 ("12.5"), whole hours above ("148"). */
export function hoursNumber(seconds: number): string {
  const h = seconds / 3600
  return h >= 100 ? Math.round(h).toLocaleString() : h.toFixed(1)
}

export function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = bytes / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`
}

/** 75.4 -> "1:15" (clip lengths, timecodes). */
export function fmtClock(seconds: number | null | undefined): string {
  const s = Math.max(0, seconds ?? 0)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = Math.floor(s % 60)
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`
}

export function timeAgoUnix(unix: number | null | undefined, now: Date = new Date()): string {
  return unix ? timeAgo(new Date(unix * 1000).toISOString(), now) : 'never'
}

/** Short monogram for games without official art (never a logo). */
export function monogram(name: string): string {
  const words = name.replace(/[^A-Za-z0-9 ]/g, ' ').split(/\s+/).filter(Boolean)
  if (!words.length) return [...name.trim()].slice(0, 2).join('') || '?' // e.g. "原神": no Latin letters to use
  if (words.length === 1) return words[0].slice(0, 3).toUpperCase()
  return words.slice(0, 3).map((w) => w[0]).join('').toUpperCase()
}

// KeyboardEvent.code -> Electron accelerator key name (layout-independent physical keys).
const SPECIAL_KEYS: Record<string, string> = {
  Space: 'Space',
  Home: 'Home',
  End: 'End',
  PageUp: 'PageUp',
  PageDown: 'PageDown',
  Insert: 'Insert',
  Pause: 'Pause',
  ScrollLock: 'Scrolllock',
  BracketLeft: '[',
  BracketRight: ']',
  Backslash: '\\',
  Semicolon: ';',
  Quote: "'",
  Comma: ',',
  Period: '.',
  Slash: '/',
  Minus: '-',
  Equal: '=',
  Backquote: '`',
  ArrowUp: 'Up',
  ArrowDown: 'Down',
  ArrowLeft: 'Left',
  ArrowRight: 'Right',
  Enter: 'Enter',
  NumpadEnter: 'Enter',
  Tab: 'Tab',
  Backspace: 'Backspace',
  Delete: 'Delete',
  PrintScreen: 'PrintScreen',
  MediaPlayPause: 'MediaPlayPause',
  MediaTrackNext: 'MediaNextTrack',
  MediaTrackPrevious: 'MediaPreviousTrack',
  MediaStop: 'MediaStop',
  // Electron's numpad names (not KeyboardEvent's): num0-num9 are handled below
  NumpadDecimal: 'numdec',
  NumpadAdd: 'numadd',
  NumpadSubtract: 'numsub',
  NumpadMultiply: 'nummult',
  NumpadDivide: 'numdiv',
}
// Keys a game or chat uses on their own: a hotkey needs a modifier with them.
const NEEDS_MODIFIER = /^([A-Z0-9]|Up|Down|Left|Right|Enter|Tab|Backspace|Delete|Space)$/

/** KeyboardEvent -> Electron accelerator ("Alt+F8", "CommandOrControl+Shift+K"). */
export function accelerator(e: Pick<KeyboardEvent, 'key' | 'code' | 'ctrlKey' | 'altKey' | 'shiftKey' | 'metaKey'>): string | null {
  if (['Control', 'Alt', 'Shift', 'Meta'].includes(e.key)) return null
  let key: string
  if (/^F\d{1,2}$/.test(e.key)) key = e.key
  else if (e.code.startsWith('Key')) key = e.code.slice(3)
  else if (e.code.startsWith('Digit')) key = e.code.slice(5)
  else if (/^Numpad\d$/.test(e.code)) key = `num${e.code.slice(6)}`
  else key = SPECIAL_KEYS[e.code] ?? ''
  if (!key) return null
  const mods = [e.ctrlKey && 'CommandOrControl', e.altKey && 'Alt', e.shiftKey && 'Shift'].filter(Boolean)
  // A bare letter or arrow would fire while typing in chat or moving in game: require a modifier.
  if (!mods.length && NEEDS_MODIFIER.test(key)) return null
  return [...mods, key].join('+')
}

/**
 * Rough recording size for fast-moving gameplay (what the Settings page shows).
 * Constant-quality rates come from typical NVENC output for 1080p60 action games.
 */
export function estimateSize(
  s: { rate_control: string; bitrate_mbps: number; quality: string; codec: string; resolution: string; fps: number; buffer_seconds: number },
  monitorHeight = 1080,
): { mbps: number; perClip: string } {
  let mbps: number
  if (s.rate_control === 'bitrate') {
    mbps = s.bitrate_mbps
  } else {
    const base: Record<string, number> = { low: 8, medium: 15, high: 28, ultra: 45, max: 75 }
    const codec: Record<string, number> = { h264: 1, hevc: 0.6, av1: 0.5 }
    const height = s.resolution === 'native' ? monitorHeight : Number(s.resolution)
    mbps = (base[s.quality] ?? 28) * (codec[s.codec] ?? 1) * (height / 1080) ** 2 * (s.fps / 60) ** 0.8
  }
  mbps = Math.round(mbps)
  return { mbps, perClip: fmtBytes((mbps * 1e6 * s.buffer_seconds) / 8) }
}

/** Short tags for the rail and ticker (three characters at most, to fit the glyph box). */
export const GAME_GLYPH: Record<string, string> = {
  lol: 'LOL',
  valorant: 'VAL',
  cs2: 'CS2',
  rocketleague: 'RL',
  dota2: 'D2',
  deadlock: 'DL',
  cod: 'COD',
  tft: 'TFT',
  pubg: 'PBG',
  brawlstars: 'BS',
  clashroyale: 'CR',
  osu: 'OSU',
  chesscom: 'CC',
  lichess: 'LI',
}
