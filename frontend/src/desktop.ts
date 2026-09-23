/**
 * Desktop-mode API (library, launcher, playtime, capture, media hub).
 *
 * The Electron preload exposes `window.clutchDesktop` with a per-launch token.
 * In the browser (dev against `clutch serve --desktop`), `?token=...` in the URL
 * is remembered for the session instead.
 */
import { useEffect, useRef } from 'react'
import { ApiError } from './api'

export interface DesktopBridge {
  token: string
  platform: string
  version: string
  pickExecutable: () => Promise<string | null>
  pickFolder: () => Promise<string | null>
  reloadHotkeys: () => void
  setLoginItem: (open: boolean) => void
  displays: () => Promise<{ id: number; label: string; primary: boolean; width: number; height: number }[]>
}

declare global {
  interface Window {
    clutchDesktop?: DesktopBridge
  }
}

function readToken(): string | null {
  if (window.clutchDesktop?.token) return window.clutchDesktop.token
  const fromUrl = new URLSearchParams(window.location.search).get('token')
  try {
    if (fromUrl) sessionStorage.setItem('clutch-token', fromUrl)
    return fromUrl ?? sessionStorage.getItem('clutch-token')
  } catch {
    return fromUrl
  }
}

export const desktopToken = readToken()
export const isDesktop = Boolean(desktopToken)
export const bridge = window.clutchDesktop

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response
  try {
    res = await fetch(`/api/desktop${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', 'X-Clutch-Token': desktopToken ?? '', ...(init.headers ?? {}) },
    })
  } catch {
    throw new ApiError('Can’t reach Clutch’s local service.', 'NETWORK', 0)
  }
  const body = await res.json().catch(() => null)
  if (!res.ok) {
    const err = body?.detail ?? body
    throw new ApiError(err?.message ?? `Request failed (${res.status})`, err?.error ?? 'ERROR', res.status)
  }
  return body as T
}

const post = <T>(path: string, body?: unknown) => call<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

/** A URL for <img>/<video> (which can't send headers) that carries the token. */
export function mediaUrl(path: string): string {
  return `/api/desktop${path}${path.includes('?') ? '&' : '?'}token=${encodeURIComponent(desktopToken ?? '')}`
}

// ── types ────────────────────────────────────────────────────────────────────

export interface Art {
  cover: string | null
  hero: string | null
  header: string | null
  logo: string | null
  steam_appid: number | null
  accent: string | null
}

export interface LibraryGame {
  id: string
  name: string
  source: string
  install_dir: string
  exe: string | null
  art: Art
  playtime: number
  week: number
  last_played: number | null
  running: boolean
  clips: number
  hidden: boolean
  stats_game: string | null
  tags: string[]
}

export interface BufferStatus {
  active: boolean
  encoder: string | null
  buffer_seconds: number
  buffered_seconds: number
  recording: boolean
  recording_seconds: number | null
  audio: string[]
  error: string | null
  fps: number
}

export interface NowPlaying {
  game_id: string
  game_name: string
  started_at: number
  seconds: number
}

export interface DesktopStatus {
  buffer: BufferStatus
  now_playing: NowPlaying[]
  clips: { count: number; bytes: number; seconds: number; by_kind: Record<string, number> }
  clips_dir: string
}

export interface Clip {
  id: number
  path: string
  kind: 'clip' | 'recording' | 'screenshot' | 'export'
  game_id: string | null
  game_name: string | null
  title: string
  created_at: number
  duration: number | null
  width: number | null
  height: number | null
  size: number
  favorite: boolean
  thumb: string | null
  parent_id: number | null
  exists: boolean
}

export interface PlaytimeGame {
  game_id: string
  game_name: string
  seconds: number
  sessions: number
  last_played: number
  week: number
  month: number
}

export interface Session {
  id: number
  game_id: string
  game_name: string
  started_at: number
  ended_at: number
  seconds: number
  active: boolean
}

export interface PlaytimeDay {
  date: string
  seconds: number
  games: Record<string, number>
}

export interface Playtime {
  now: NowPlaying[]
  games: PlaytimeGame[]
  daily: PlaytimeDay[]
  sessions: Session[]
}

export interface Settings {
  clips_dir: string
  buffer_seconds: number
  fps: number
  quality: 'low' | 'medium' | 'high' | 'ultra'
  encoder: 'auto' | 'nvenc' | 'amf' | 'qsv' | 'x264'
  monitor: number
  record_system_audio: boolean
  record_mic: boolean
  auto_buffer: boolean
  hotkey_clip: string
  hotkey_record: string
  hotkey_screenshot: string
  start_with_windows: boolean
  minimize_to_tray: boolean
  notify_sessions: boolean
  auto_sync_on_exit: boolean
  linked_profiles: Record<string, string>
}

export type DesktopEvent =
  | { type: 'clip_saved'; at: number; clip: Clip }
  | ({ type: 'buffer'; at: number; reason?: string } & BufferStatus)
  | ({ type: 'recording'; at: number } & BufferStatus)
  | { type: 'game_started'; at: number; game_id: string; game_name: string; stats_game: string | null }
  | { type: 'session_end'; at: number; game_id: string; game_name: string; seconds: number; clips: number }
  | { type: 'launching'; at: number; game_id: string; game_name: string }
  | { type: 'stats_synced'; at: number; game: string; key: string; new: number }
  | { type: 'error'; at: number; message: string }

// ── endpoints ────────────────────────────────────────────────────────────────

const enc = encodeURIComponent

export const desktop = {
  status: () => call<DesktopStatus>('/status'),
  settings: () => call<Settings>('/settings'),
  saveSettings: (patch: Partial<Settings>) => call<Settings>('/settings', { method: 'PUT', body: JSON.stringify(patch) }),

  library: (hidden = false) => call<LibraryGame[]>(`/library${hidden ? '?hidden=true' : ''}`),
  scan: () => post<LibraryGame[]>('/library/scan'),
  launch: (id: string) => post<{ ok: true }>(`/library/${enc(id)}/launch`),
  setHidden: (id: string, hidden: boolean) => post<{ ok: true }>(`/library/${enc(id)}/hidden`, { hidden }),
  addCustom: (name: string, exe: string) => post<LibraryGame>('/library/custom', { name, exe }),
  iconUrl: (id: string) => mediaUrl(`/library/${enc(id)}/icon`),

  playtime: (days = 182) => call<Playtime>(`/playtime?days=${days}`),
  gameSessions: (id: string) => call<Session[]>(`/playtime/${enc(id)}/sessions`),

  setBuffer: (on: boolean) => post<BufferStatus>('/capture/buffer', { on }),
  clip: (seconds?: number) => post<Clip>('/capture/clip', seconds ? { seconds } : {}),
  record: () => post<{ recording: boolean; clip?: Clip }>('/capture/record'),
  screenshot: () => post<Clip>('/capture/screenshot'),

  clips: (params: { game_id?: string; kind?: string; favorites?: boolean; q?: string } = {}) => {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '' && v !== false) qs.set(k, String(v))
    return call<{ items: Clip[]; stats: DesktopStatus['clips'] }>(`/clips?${qs}`)
  },
  updateClip: (id: number, patch: { title?: string; favorite?: boolean }) => call<Clip>(`/clips/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteClip: (id: number) => call<{ ok: true }>(`/clips/${id}`, { method: 'DELETE' }),
  trim: (id: number, start: number, end: number) => post<Clip>(`/clips/${id}/trim`, { start, end, precise: true }),
  gif: (id: number, start: number, end: number) => post<Clip>(`/clips/${id}/gif`, { start, end }),
  compact: (id: number, start: number, end: number, target_mb = 9.5) => post<Clip>(`/clips/${id}/compact`, { start, end, target_mb }),
  reveal: (id: number) => post<{ ok: true }>(`/clips/${id}/reveal`),
  importClips: () => post<{ added: number; removed: number }>('/clips/import'),
  clipFile: (id: number) => mediaUrl(`/clips/${id}/file`),
  clipThumb: (id: number) => mediaUrl(`/clips/${id}/thumb`),
}

// ── live events ──────────────────────────────────────────────────────────────

type Listener = (e: DesktopEvent) => void
const listeners = new Set<Listener>()
let source: EventSource | null = null

function ensureSource() {
  if (source || !isDesktop) return
  source = new EventSource(mediaUrl('/events'))
  const types: DesktopEvent['type'][] = ['clip_saved', 'buffer', 'recording', 'game_started', 'session_end', 'launching', 'stats_synced', 'error']
  for (const t of types) {
    source.addEventListener(t, (msg) => {
      const event = JSON.parse((msg as MessageEvent).data) as DesktopEvent
      for (const l of listeners) l(event)
    })
  }
}

/** Subscribe to desktop events (clip saved, game started, ...) for the lifetime of a component. */
export function useDesktopEvents(handler: Listener) {
  const ref = useRef(handler)
  ref.current = handler
  useEffect(() => {
    if (!isDesktop) return
    ensureSource()
    const l: Listener = (e) => ref.current(e)
    listeners.add(l)
    return () => {
      listeners.delete(l)
    }
  }, [])
}
