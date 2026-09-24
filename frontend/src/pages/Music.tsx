import { useEffect, useRef, useState } from 'react'
import { Hotkey, Toggle } from '../components/Controls'
import { MusicIcon, PlayIcon, VolumeIcon } from '../components/Icons'
import { appAccent, Art, SeekBar, Transport, useMedia } from '../components/Music'
import { useDesktop } from '../components/Shell'
import { bridge, desktop, type MediaSession, type MusicApp, type Settings } from '../desktop'
import { useAsync } from '../hooks'
import { DesktopOnly } from './DesktopOnly'

export function Music() {
  return (
    <DesktopOnly feature="Music controls">
      <MusicInner />
    </DesktopOnly>
  )
}

const LOGO: Record<MusicApp['key'], string> = { spotify: 'SP', applemusic: 'AM', ytmusic: 'YT' }

function Volume({ s, onChange }: { s: MediaSession; onChange: (patch: { level?: number; muted?: boolean }) => Promise<void> }) {
  const [draft, setDraft] = useState<number | null>(null)
  const timer = useRef<number | undefined>(undefined)
  if (!s.volume) return null
  const level = draft ?? Math.round(s.volume.level * 100)
  return (
    <div className="np-volume" title={s.volume_scope === 'browser' ? `${s.app_name} plays inside the browser, so this is the browser's volume` : undefined}>
      <button className={`tbtn ghost ${s.volume.muted ? 'on' : ''}`} aria-pressed={s.volume.muted} title={s.volume.muted ? 'Unmute' : 'Mute'} onClick={() => onChange({ muted: !s.volume!.muted })}>
        <VolumeIcon muted={s.volume.muted} />
      </button>
      <input
        type="range"
        min={0}
        max={100}
        value={level}
        aria-label={`${s.app_name} volume`}
        onChange={(e) => {
          const v = Number(e.target.value)
          setDraft(v)
          window.clearTimeout(timer.current)
          timer.current = window.setTimeout(() => {
            // keep showing the new level until the mixer confirms it (no snap back)
            void onChange({ level: v / 100 }).finally(() => setDraft((d) => (d === v ? null : d)))
          }, 120)
        }}
      />
      <span className="mono">{level}%</span>
      {s.volume_scope === 'browser' && <span className="hint" style={{ margin: 0 }}>browser volume</span>}
    </div>
  )
}

function MusicInner() {
  const { settings, setSettings, toast } = useDesktop()
  const { state, setState, run } = useMedia()
  const apps = useAsync(() => desktop.musicApps(), [])
  const [picked, setPicked] = useState<string | null>(null)
  const sessions = state?.sessions.filter((s) => s.title) ?? []
  const s = sessions.find((x) => x.id === picked) ?? sessions[0] ?? null

  useEffect(() => {
    if (picked && !sessions.some((x) => x.id === picked)) setPicked(null)
  }, [picked, sessions])

  async function save(patch: Partial<Settings>) {
    try {
      setSettings(await desktop.saveSettings(patch))
      if (Object.keys(patch).some((k) => k.startsWith('hotkey_'))) bridge?.reloadHotkeys()
    } catch (e) {
      toast({ title: 'Couldn’t save that', sub: (e as Error).message, tone: 'error' })
    }
  }

  async function volume(session: MediaSession, patch: { level?: number; muted?: boolean }) {
    try {
      setState(await desktop.mediaVolume(session.id, patch))
    } catch (e) {
      toast({ title: 'Volume', sub: (e as Error).message, tone: 'error' })
    }
  }

  async function open(app: MusicApp) {
    try {
      const r = await desktop.launchMusicApp(app.key)
      toast({ title: `Opening ${app.name}`, sub: r.opened === 'web' ? 'In your browser' : undefined })
    } catch (e) {
      toast({ title: `Couldn’t open ${app.name}`, sub: (e as Error).message, tone: 'error' })
    }
  }

  return (
    <div>
      <div className="page-head" data-echo="music">
        <div>
          <div className="kicker bare">
            <b>07</b> Music
          </div>
          <h1>
            Your <em>soundtrack</em>
          </h1>
        </div>
        <div className="aside">
          <span className="mono muted" style={{ fontSize: 12, maxWidth: 360, textAlign: 'right' }}>
            Controls any app in Windows’ media controls: Spotify, Apple Music, YouTube Music and more. No sign-in.
          </span>
        </div>
      </div>

      <div className="grid cols-2-1">
        <div className="stack">
          {!state || (!state.available && !state.error) ? (
            <div className="skeleton" style={{ height: 290 }} aria-label="Connecting to Windows media controls" />
          ) : !state.available ? (
            <div className="card empty">
              <span className="display">Music controls unavailable</span>
              {state.error}
            </div>
          ) : s ? (
            <section className={`now-playing ${s.status === 'playing' ? 'playing' : ''}`} style={{ '--app': appAccent(s.app) } as React.CSSProperties}>
              {s.art && <img className="np-bg" src={desktop.mediaArt(s.art)} alt="" />}
              <div className="np-body">
                <Art s={s} size={210} className="np-art" />
                <div className="np-meta">
                  <span className="np-app">
                    <i /> {s.app_name} · {s.status === 'playing' ? 'playing' : s.status}
                  </span>
                  <h2 title={s.title}>{s.title}</h2>
                  <div className="np-artist">{s.artist}</div>
                  {s.album && s.album !== s.title && <div className="np-album">{s.album}</div>}
                  <SeekBar s={s} at={state!.at} run={run} />
                  <div className="np-controls">
                    <Transport s={s} run={run} big />
                    <Volume s={s} onChange={(p) => volume(s, p)} />
                  </div>
                </div>
              </div>
            </section>
          ) : (
            <div className="card empty np-empty">
              <MusicIcon className="empty-icon" />
              <span className="display">Nothing playing</span>
              Start something in Spotify, Apple Music or YouTube Music and it shows up here, with controls that also work in game.
            </div>
          )}

          {sessions.length > 1 && (
            <section className="card">
              <h2>Also open</h2>
              <div className="sessions">
                {sessions.map((x) => (
                  <button key={x.id} className={`session-row ${x.id === s?.id ? 'on' : ''}`} onClick={() => setPicked(x.id)} style={{ '--app': appAccent(x.app) } as React.CSSProperties}>
                    <Art s={x} size={36} />
                    <span style={{ minWidth: 0, flex: 1, textAlign: 'left' }}>
                      <span className="t">{x.title}</span>
                      <span className="s">
                        {x.artist} · {x.app_name}
                      </span>
                    </span>
                    <span className="mono muted" style={{ fontSize: 11 }}>
                      {x.status}
                    </span>
                  </button>
                ))}
              </div>
            </section>
          )}
        </div>

        <div className="stack">
          <section className="card">
            <h2>Music apps</h2>
            <div className="music-apps">
              {(apps.data ?? []).map((a) => (
                <button key={a.key} className="music-app" style={{ '--app': a.accent } as React.CSSProperties} onClick={() => void open(a)}>
                  <span className="logo">{LOGO[a.key]}</span>
                  <span style={{ minWidth: 0, flex: 1, textAlign: 'left' }}>
                    <span className="t">{a.name}</span>
                    <span className="s">{a.installed ? 'Installed · open the app' : 'Not installed · opens the website'}</span>
                  </span>
                  <PlayIcon />
                </button>
              ))}
            </div>
          </section>

          {settings && (
            <section className="card compact-settings">
              <h2>In game</h2>
              <div className="setting">
                <div>
                  <div className="t">Play / pause</div>
                  <div className="h">Works over your game; a toast shows what’s playing.</div>
                </div>
                <div className="control">
                  <Hotkey value={settings.hotkey_media_play} onChange={(v) => void save({ hotkey_media_play: v })} />
                </div>
              </div>
              <div className="setting">
                <div>
                  <div className="t">Next track</div>
                </div>
                <div className="control">
                  <Hotkey value={settings.hotkey_media_next} onChange={(v) => void save({ hotkey_media_next: v })} />
                </div>
              </div>
              <div className="setting">
                <div>
                  <div className="t">Previous track</div>
                </div>
                <div className="control">
                  <Hotkey value={settings.hotkey_media_prev} onChange={(v) => void save({ hotkey_media_prev: v })} />
                </div>
              </div>
              <div className="setting">
                <div>
                  <div className="t">Turn music down while gaming</div>
                  <div className="h">When a game starts, music apps drop to {settings.music_duck_level}% of their volume, and come back when you quit.</div>
                </div>
                <div className="control">
                  <Toggle label="Turn music down while gaming" on={settings.music_duck} onChange={(v) => void save({ music_duck: v })} />
                </div>
              </div>
              {settings.music_duck && (
                <div className="setting">
                  <div>
                    <div className="t">Music level in game</div>
                  </div>
                  <div className="control">
                    <input type="range" min={5} max={90} step={5} value={settings.music_duck_level} onChange={(e) => void save({ music_duck_level: Number(e.target.value) })} />
                    <span className="mono" style={{ width: 44, textAlign: 'right' }}>
                      {settings.music_duck_level}%
                    </span>
                  </div>
                </div>
              )}
            </section>
          )}
        </div>
      </div>
    </div>
  )
}
