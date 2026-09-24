import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { bridge, desktop, isDesktop, useDesktopEvents, type Clip, type DesktopEvent, type DesktopStatus, type Settings } from '../desktop'
import { fmtBytes, fmtHours, GAME_GLYPH, monogram } from '../format'
import { useGames } from '../hooks'
import { CameraIcon, ClipIcon, ClockIcon, GearIcon, HomeIcon, LibraryIcon, RecIcon, ReportIcon, StatsIcon, TargetIcon, UsersIcon } from './Icons'
import { Onboarding } from './Onboarding'

// ── shared desktop state ────────────────────────────────────────────────────

interface DesktopState {
  status: DesktopStatus | null
  settings: Settings | null
  refresh: () => void
  setSettings: (s: Settings) => void
  toast: (t: Omit<Toast, 'id'>) => void
}

const Ctx = createContext<DesktopState>({ status: null, settings: null, refresh: () => {}, setSettings: () => {}, toast: () => {} })
export const useDesktop = () => useContext(Ctx)

interface Toast {
  id: number
  title: string
  sub?: string
  thumb?: string
  tone?: 'ok' | 'error'
  onClick?: () => void
}

export function DesktopProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<DesktopStatus | null>(null)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [toasts, setToasts] = useState<Toast[]>([])
  const navigate = useNavigate()

  const refresh = useCallback(() => {
    if (!isDesktop) return
    desktop.status().then(setStatus, () => {})
  }, [])

  const toast = useCallback((t: Omit<Toast, 'id'>) => {
    const id = Date.now() + Math.random()
    setToasts((ts) => [...ts.slice(-3), { ...t, id }])
    setTimeout(() => setToasts((ts) => ts.filter((x) => x.id !== id)), 4800)
  }, [])

  useEffect(() => {
    if (!isDesktop) return
    refresh()
    desktop.settings().then(setSettings, () => {})
    document.documentElement.classList.add('is-desktop')
    const id = setInterval(refresh, 4000)
    return () => clearInterval(id)
  }, [refresh])

  useDesktopEvents((e: DesktopEvent) => {
    refresh()
    if (e.type === 'clip_saving') {
      toast({ title: 'Clipping…', sub: e.game_name ?? 'Desktop' })
    } else if (e.type === 'clip_saved') {
      const c: Clip = e.clip
      const label = c.kind === 'screenshot' ? 'Screenshot saved' : c.kind === 'recording' ? 'Recording saved' : 'Clip saved'
      // The thumbnail is rendered just after the clip is announced, so it isn't shown here.
      toast({ title: label, sub: `${c.game_name ?? 'Desktop'}${c.duration ? ` · ${Math.round(c.duration)}s` : ''}`, onClick: () => navigate(`/clips?open=${c.id}`) })
    } else if (e.type === 'game_started') {
      toast({ title: `${e.game_name} is running`, sub: 'Tracking playtime · replay buffer armed' })
    } else if (e.type === 'session_end') {
      toast({ title: `${e.game_name} · ${fmtHours(e.seconds)}`, sub: `Session over${e.clips ? ` · ${e.clips} clip${e.clips > 1 ? 's' : ''}` : ''}` })
    } else if (e.type === 'stats_synced' && e.new > 0) {
      toast({ title: `${e.new} new match${e.new > 1 ? 'es' : ''} synced`, sub: e.game })
    } else if (e.type === 'highlight') {
      toast({ title: `Auto-clip · ${e.title}`, sub: 'Saving a few seconds after the play' })
    } else if (e.type === 'report_ready') {
      const st = e.report.stats
      toast({
        title: `${e.report.game_name} report card`,
        sub: st ? `${st.wins}W ${st.losses}L · ${fmtHours(e.report.seconds)}` : `${fmtHours(e.report.seconds)} · ${e.report.clips.length} clips`,
        onClick: () => navigate('/sessions'),
      })
    } else if (e.type === 'goal') {
      const title = e.state === 'done' ? 'Goal reached' : e.state === 'over' ? 'Over your daily cap' : 'Close to your daily cap'
      toast({ title, sub: `${e.goal.progress.value ?? ''} ${e.goal.progress.unit}`.trim(), tone: e.state === 'over' ? 'error' : 'ok', onClick: () => navigate('/goals') })
    } else if (e.type === 'storage_cleaned') {
      toast({ title: `Cleaned up ${e.count} old clip${e.count === 1 ? '' : 's'}`, sub: `${fmtBytes(e.bytes)} moved to the Recycle Bin` })
    } else if (e.type === 'error') {
      toast({ title: 'Something went wrong', sub: e.message, tone: 'error' })
    }
  })

  return (
    <Ctx.Provider value={{ status, settings, refresh, setSettings, toast }}>
      {children}
      {isDesktop && <Onboarding />}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.tone === 'error' ? 'error' : ''}`} onClick={t.onClick} style={{ cursor: t.onClick ? 'pointer' : undefined }}>
            {t.thumb ? <img src={t.thumb} alt="" /> : <span className={`rec-dot ${t.tone === 'error' ? 'on' : 'buffer'}`} />}
            <div style={{ minWidth: 0 }}>
              <div className="t">{t.title}</div>
              {t.sub && <div className="s">{t.sub}</div>}
            </div>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  )
}

// ── chrome ──────────────────────────────────────────────────────────────────

export function Wordmark() {
  return (
    <span className="rail-brand">
      <svg width="28" height="28" viewBox="0 0 32 32" aria-hidden>
        <path d="M0 0h24l8 8v24H0z" fill="#d4ff3a" />
        <path d="M22 11.5A7.5 7.5 0 1 0 22 20.5" fill="none" stroke="#0b0d02" strokeWidth="4.2" />
        <circle cx="25.5" cy="25.5" r="2.4" fill="#ff3b30" />
      </svg>
      <span className="name">
        CLUTCH<span>.</span>
      </span>
    </span>
  )
}

export function Titlebar() {
  const { status } = useDesktop()
  if (!isDesktop) return null
  const b = status?.buffer
  const playing = status?.now_playing[0]
  return (
    <div className="titlebar">
      <span className="grow" />
      {playing && (
        <span className="kicker bare" style={{ color: 'var(--up)' }}>
          ● {playing.game_name} · {fmtHours(playing.seconds)}
        </span>
      )}
      {b?.recording ? (
        <span className="kicker bare" style={{ color: 'var(--rec)' }}>
          <span className="rec-dot on" /> REC {Math.floor((b.recording_seconds ?? 0) / 60)}:{String(Math.floor((b.recording_seconds ?? 0) % 60)).padStart(2, '0')}
        </span>
      ) : b?.active ? (
        <span className="kicker bare">
          <span className="eq" aria-hidden>
            <i />
            <i />
            <i />
            <i />
          </span>{' '}
          Buffer {b.buffer_seconds}s
        </span>
      ) : null}
    </div>
  )
}


export function Rail() {
  const games = useGames()
  const { status } = useDesktop()
  const clipCount = status?.clips.count
  return (
    <nav className="rail" aria-label="Main">
      <NavLink to="/" aria-label="Clutch home" style={{ display: 'block' }}>
        <Wordmark />
      </NavLink>
      <div className="rail-scroll">
        <NavLink to="/" end className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
          <HomeIcon /> <span className="label-text">Home</span>
        </NavLink>
        <NavLink to="/library" className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
          <LibraryIcon /> <span className="label-text">Library</span>
          {status?.now_playing.length ? <span className="live" title="A game is running" /> : null}
        </NavLink>
        <NavLink to="/clips" className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
          <ClipIcon /> <span className="label-text">Clips</span>
          {clipCount ? <span className="count">{clipCount}</span> : null}
        </NavLink>
        <NavLink to="/playtime" className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
          <ClockIcon /> <span className="label-text">Playtime</span>
        </NavLink>
        {isDesktop && (
          <>
            <NavLink to="/sessions" className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
              <ReportIcon /> <span className="label-text">Sessions</span>
            </NavLink>
            <NavLink to="/goals" className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
              <TargetIcon /> <span className="label-text">Goals</span>
            </NavLink>
            <NavLink to="/friends" className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
              <UsersIcon /> <span className="label-text">Friends</span>
            </NavLink>
          </>
        )}

        <div className="rail-group kicker">
          <b>//</b> Stats
        </div>
        <NavLink to="/stats" end className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
          <StatsIcon /> <span className="label-text">Player search</span>
        </NavLink>
        {games.map((g) => (
          <NavLink
            key={g.id}
            to={`/${g.id}`}
            className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
            style={{ '--game': g.accent } as React.CSSProperties}
            title={`${g.name}${g.configured ? '' : ' (demo data — no API key)'}`}
          >
            <span className="glyph">{GAME_GLYPH[g.id] ?? monogram(g.name)}</span>
            <span className="label-text">{g.name}</span>
          </NavLink>
        ))}
        <div className="rail-group" />
        <NavLink to="/settings" className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
          <GearIcon /> <span className="label-text">Settings</span>
        </NavLink>
      </div>
      {isDesktop && <CaptureDock />}
    </nav>
  )
}

function CaptureDock() {
  const { status, settings, refresh, toast } = useDesktop()
  const b = status?.buffer
  const [busy, setBusy] = useState<string | null>(null)

  async function act(name: string, fn: () => Promise<unknown>) {
    setBusy(name)
    try {
      await fn()
    } catch (e) {
      toast({ title: (e as Error).message, tone: 'error' })
    } finally {
      setBusy(null)
      refresh()
    }
  }

  const state = b?.recording ? 'Recording' : b?.active ? 'Replay buffer' : 'Capture off'
  const sub = b?.recording
    ? `${Math.round(b.recording_seconds ?? 0)}s · ${b.encoder?.toUpperCase()}`
    : b?.active
      ? `${Math.min(b.buffered_seconds, b.buffer_seconds).toFixed(0)}/${b.buffer_seconds}s · ${b.encoder?.toUpperCase()}${b.audio.length ? ' · audio' : ''}`
      : settings?.auto_buffer
        ? 'arms when a game starts'
        : 'toggle to start'
  return (
    <div className={`dock ${b?.recording ? 'recording' : b?.active ? 'armed' : ''}`}>
      <div className="dock-status">
        <span className={`rec-dot ${b?.recording ? 'on' : b?.active ? 'buffer' : ''}`} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="label">{state}</div>
          <div className="sub">{b?.error ? <span className="error">{b.error}</span> : sub}</div>
        </div>
        <button
          className="switch"
          role="switch"
          aria-checked={Boolean(b?.active)}
          aria-label="Replay buffer"
          disabled={busy !== null}
          onClick={() => void act('buffer', () => desktop.setBuffer(!b?.active))}
        />
      </div>
      <div className="dock-actions">
        <button className="dock-btn hot" disabled={busy !== null} onClick={() => void act('clip', () => desktop.clip())} title="Save the last seconds">
          <ClipIcon className="" />
          Clip<kbd>{settings?.hotkey_clip ?? 'F8'}</kbd>
        </button>
        <button className={`dock-btn ${b?.recording ? 'rec' : ''}`} disabled={busy !== null} onClick={() => void act('rec', () => desktop.record())}>
          <RecIcon />
          {b?.recording ? 'Stop' : 'Rec'}
          <kbd>{settings?.hotkey_record ?? 'F9'}</kbd>
        </button>
        <button className="dock-btn" disabled={busy !== null} onClick={() => void act('shot', () => desktop.screenshot())}>
          <CameraIcon />
          Shot<kbd>{settings?.hotkey_screenshot ?? 'F10'}</kbd>
        </button>
      </div>
    </div>
  )
}

export { bridge }
