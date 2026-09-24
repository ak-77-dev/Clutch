/**
 * Desktop-mode API (library, launcher, playtime, capture, media hub).
 *
 * The Electron preload exposes `window.clutchDesktop` with a per-launch token.
 * In the browser (dev against `clutch serve --desktop`), `?token=...` in the URL
 * is remembered for the session instead.
 */
import { useEffect, useRef } from 'react'
import { ApiError } from './api'
import type { MatchSummary } from './types'

export interface DesktopBridge {
  token: string
  platform: string
  version: string
  pickExecutable: () => Promise<string | null>
  pickFolder: () => Promise<string | null>
  reloadHotkeys: () => void
  setLoginItem: (open: boolean) => void
  displays: () => Promise<{ id: number; label: string; primary: boolean; width: number; height: number }[]>
  openExternal?: (url: string) => void
  copy?: (text: string) => void
  packaged?: boolean
  checkUpdates?: () => void
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
  codec?: string | null
  audio_restarts?: number
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
  share_url?: string | null
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
  quality: 'low' | 'medium' | 'high' | 'ultra' | 'max'
  encoder: 'auto' | 'nvenc' | 'amf' | 'qsv' | 'x264'
  codec: 'h264' | 'hevc' | 'av1'
  preset: 'speed' | 'balanced' | 'quality'
  rate_control: 'quality' | 'bitrate'
  bitrate_mbps: number
  resolution: 'native' | '1440' | '1080' | '720'
  audio_kbps: number
  audio_device: string
  mic_device: string
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
  onboarded: boolean
  storage_max_days: number
  storage_max_gb: number
  separate_audio_tracks: boolean
  auto_clip: boolean
  auto_clip_min: 'kill' | 'multikill' | 'ace'
  overlay_enabled: boolean
  hotkey_overlay: string
  discord_rpc: boolean
  discord_client_id: string
  steamgriddb_key: string
  share_host: 'catbox' | 'litterbox'
  hotkey_media_play: string
  hotkey_media_next: string
  hotkey_media_prev: string
  music_duck: boolean
  music_duck_level: number
}

export interface MediaSession {
  id: string
  title: string
  artist: string
  album: string
  status: 'playing' | 'paused' | 'stopped' | 'closed' | 'opened' | 'changing'
  position: number
  duration: number
  can: { play_pause: boolean; next: boolean; previous: boolean; seek: boolean; shuffle: boolean; repeat: boolean }
  shuffle: boolean | null
  repeat: 'none' | 'track' | 'list' | null
  app: 'spotify' | 'applemusic' | 'ytmusic' | 'browser' | 'other'
  app_name: string
  art: string | null
  volume: { level: number; muted: boolean } | null
  volume_scope: 'app' | 'browser'
}

export interface MediaState {
  available: boolean
  current: string | null
  sessions: MediaSession[]
  at: number
}

export interface MusicApp {
  key: 'spotify' | 'applemusic' | 'ytmusic'
  name: string
  accent: string
  installed: boolean
  opens: 'app' | 'web'
}

export type MediaAction = 'play_pause' | 'play' | 'pause' | 'next' | 'previous' | 'seek' | 'shuffle' | 'repeat'

export interface ReportStats {
  games: number
  wins: number
  losses: number
  metric: { key: string; label: string; fmt: string; session: number | null; usual: number | null }
  best: { id: string; character: string; value: number; result: string; score_line: string } | null
  rank: string | null
}

export interface Report {
  id: number
  session_id: number | null
  game_id: string
  game_name: string
  started_at: number
  ended_at: number
  seconds: number
  clips: number[]
  stats: ReportStats | null
  stats_game: string | null
}

export type GoalKind = 'daily_cap' | 'weekly_hours' | 'clips' | 'win_rate' | 'rank'

export interface GoalProgress {
  value: number | null
  target: number
  unit: string
  progress: number
  state: 'ok' | 'close' | 'over' | 'done' | 'unlinked' | 'unranked'
  current?: string | null
}

export interface Goal {
  id: number
  kind: GoalKind
  game_id: string | null
  target: number
  label: string | null
  created_at: number
  done_at: number | null
  notified: string | null
  progress: GoalProgress
}

export interface FriendCard {
  game: string
  key: string
  name: string
  added_at: number
  synced_at: number
  tag?: string | null
  icon?: string | null
  rank?: string | null
  last_played?: string | null
  wins?: number
  losses?: number
  demo?: boolean
  error?: boolean
}

export interface FriendItem extends MatchSummary {
  friend: string
  friend_key: string
  icon: string | null
}

export interface SessionView {
  playing: NowPlaying | null
  today_seconds: number
  buffer: BufferStatus
  session_clips?: number
  today?: { wins: number; losses: number }
  rank?: string | null
  daily_cap?: GoalProgress
}

export interface AutoClipGame {
  game_id: string
  name: string
  method: string
  needs_install: boolean
  installed: boolean
}

export interface StorageInfo {
  count: number
  bytes: number
  seconds: number
  by_kind: Record<string, number>
  plan: { count: number; bytes: number; clip_ids: number[]; reasons: Record<string, string> }
}

export interface ApiKeys {
  path: string
  keys: { name: string; game: string; label: string; help: string; set: boolean; preview: string }[]
  lol_platform: string
}

export interface ClipMatch {
  game: string
  key: string
  id: string
  date: string
  result: string
  character: string
  score_line: string
  map: string | null
  mode: string
}

export interface Capabilities {
  encoders: Record<'nvenc' | 'amf' | 'qsv' | 'x264', Record<string, boolean>>
  audio: { outputs: string[]; inputs: string[] }
}

export type DesktopEvent =
  | { type: 'clip_saved'; at: number; clip: Clip }
  | { type: 'clip_updated'; at: number; clip: Clip }
  | { type: 'clip_saving'; at: number; game_name: string | null }
  | ({ type: 'buffer'; at: number; reason?: string } & BufferStatus)
  | ({ type: 'recording'; at: number } & BufferStatus)
  | { type: 'game_started'; at: number; game_id: string; game_name: string; stats_game: string | null }
  | { type: 'session_end'; at: number; game_id: string; game_name: string; seconds: number; clips: number }
  | { type: 'launching'; at: number; game_id: string; game_name: string }
  | { type: 'stats_synced'; at: number; game: string; key: string; new: number }
  | { type: 'error'; at: number; message: string }
  | { type: 'highlight'; at: number; game_id: string; level: string; title: string }
  | { type: 'report_ready'; at: number; report: Report }
  | { type: 'goal'; at: number; goal: Goal; state: GoalProgress['state'] }
  | { type: 'storage_cleaned'; at: number; count: number; bytes: number }
  | ({ type: 'media' } & MediaState)

// ── endpoints ────────────────────────────────────────────────────────────────

const enc = encodeURIComponent

export const desktop = {
  status: () => call<DesktopStatus>('/status'),
  settings: () => call<Settings>('/settings'),
  capabilities: () => call<Capabilities>('/capabilities'),
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

  // editing & sharing
  montage: (clip_ids: number[], title?: string) => post<Clip>('/clips/montage', { clip_ids, title }),
  caption: (id: number, text: string, position: 'top' | 'bottom' = 'bottom') => post<Clip>(`/clips/${id}/caption`, { text, position }),
  vertical: (id: number, mode: 'blur' | 'crop' = 'blur') => post<Clip>(`/clips/${id}/vertical`, { mode }),
  withoutMic: (id: number) => post<Clip>(`/clips/${id}/without-mic`),
  share: (id: number) => post<Clip>(`/clips/${id}/share`),
  clipMatch: (id: number) => call<{ match: ClipMatch | null }>(`/clips/${id}/match`),
  matchClips: (game: string, key: string) => call<Record<string, number[]>>(`/matches/${enc(game)}/${enc(key)}/clips`),

  // storage
  storage: (max_days?: number, max_gb?: number) => {
    const qs = new URLSearchParams()
    if (max_days !== undefined) qs.set('max_days', String(max_days))
    if (max_gb !== undefined) qs.set('max_gb', String(max_gb))
    return call<StorageInfo>(`/storage?${qs}`)
  },
  cleanStorage: () => post<{ count: number; bytes: number }>('/storage/clean'),

  // sessions, goals, friends
  reports: (limit = 20) => call<Report[]>(`/reports?limit=${limit}`),
  session: () => call<SessionView>('/session'),
  goals: () => call<Goal[]>('/goals'),
  addGoal: (goal: { kind: GoalKind; target: number; game_id?: string | null; label?: string | null }) => post<Goal>('/goals', goal),
  deleteGoal: (id: number) => call<{ ok: true }>(`/goals/${id}`, { method: 'DELETE' }),
  friends: () => call<{ cards: FriendCard[]; items: FriendItem[] }>('/friends'),
  addFriend: (game: string, query: string) => post<FriendCard>('/friends', { game, query }),
  removeFriend: (game: string, key: string) => call<{ ok: true }>(`/friends/${enc(game)}/${enc(key)}`, { method: 'DELETE' }),
  refreshFriends: () => post<{ refreshed: number }>('/friends/refresh'),

  // auto-clip & keys
  autoclip: () => call<AutoClipGame[]>('/autoclip'),
  installAutoclip: (id: string) => post<{ path: string }>(`/autoclip/${enc(id)}/install`),
  keys: () => call<ApiKeys>('/keys'),

  // music (Windows media sessions: Spotify, Apple Music, YouTube Music, ...)
  media: () => call<MediaState>('/media'),
  mediaCommand: (action: MediaAction, session?: string | null, value?: unknown) => post<MediaState & { ok: boolean }>(`/media/${action}`, { session, value }),
  mediaVolume: (session: string, patch: { level?: number; muted?: boolean }) => post<MediaState & { ok: boolean }>('/media/volume', { session, ...patch }),
  mediaArt: (key: string) => mediaUrl(`/media/art/${key}`),
  musicApps: () => call<MusicApp[]>('/media/apps'),
  launchMusicApp: (key: string) => post<{ opened: 'app' | 'web' }>(`/media/apps/${key}/launch`),
  saveKeys: (values: Record<string, string | null>) => call<ApiKeys>('/keys', { method: 'PUT', body: JSON.stringify(values) }),
}

// ── live events ──────────────────────────────────────────────────────────────

type Listener = (e: DesktopEvent) => void
const listeners = new Set<Listener>()
let source: EventSource | null = null

function ensureSource() {
  if (source || !isDesktop) return
  source = new EventSource(mediaUrl('/events'))
  const types: DesktopEvent['type'][] = ['clip_saved', 'clip_updated', 'clip_saving', 'buffer', 'recording', 'game_started', 'session_end', 'launching', 'stats_synced', 'error', 'highlight', 'report_ready', 'goal', 'storage_cleaned', 'media']
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
