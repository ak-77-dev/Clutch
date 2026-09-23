import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useDesktop } from '../components/Shell'
import { bridge, desktop, type Settings } from '../desktop'
import { useGames } from '../hooks'
import { DesktopOnly } from './DesktopOnly'

/** KeyboardEvent -> Electron accelerator ("Alt+F8", "CommandOrControl+Shift+K"). */
export function accelerator(e: Pick<KeyboardEvent, 'key' | 'code' | 'ctrlKey' | 'altKey' | 'shiftKey' | 'metaKey'>): string | null {
  if (['Control', 'Alt', 'Shift', 'Meta'].includes(e.key)) return null
  let key: string
  if (/^F\d{1,2}$/.test(e.key)) key = e.key
  else if (e.code.startsWith('Key')) key = e.code.slice(3)
  else if (e.code.startsWith('Digit')) key = e.code.slice(5)
  else if (e.code.startsWith('Numpad')) key = `num${e.code.slice(6).toLowerCase()}`
  else key = ({ Space: 'Space', Home: 'Home', End: 'End', PageUp: 'PageUp', PageDown: 'PageDown', Insert: 'Insert', Pause: 'Pause', ScrollLock: 'Scrolllock' } as Record<string, string>)[e.code] ?? ''
  if (!key) return null
  const mods = [e.ctrlKey && 'CommandOrControl', e.altKey && 'Alt', e.shiftKey && 'Shift'].filter(Boolean)
  // A bare letter would fire while typing in chat: require a modifier unless it's a function/special key.
  if (!mods.length && /^[A-Z0-9]$/.test(key)) return null
  return [...mods, key].join('+')
}

function Hotkey({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [listening, setListening] = useState(false)
  useEffect(() => {
    if (!listening) return
    function onKey(e: KeyboardEvent) {
      e.preventDefault()
      if (e.key === 'Escape') return setListening(false)
      const acc = accelerator(e)
      if (acc) {
        onChange(acc)
        setListening(false)
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [listening, onChange])
  return (
    <button className={`hotkey ${listening ? 'listening' : ''}`} onClick={() => setListening(true)} onBlur={() => setListening(false)}>
      {listening ? 'Press a key…' : value.replace('CommandOrControl', 'Ctrl')}
    </button>
  )
}

function Toggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return <button className="switch" role="switch" aria-checked={on} aria-label={label} onClick={() => onChange(!on)} />
}

function Row({ t, h, children }: { t: string; h?: string; children: React.ReactNode }) {
  return (
    <div className="setting">
      <div>
        <div className="t">{t}</div>
        {h && <div className="h">{h}</div>}
      </div>
      <div className="control">{children}</div>
    </div>
  )
}

export function SettingsPage() {
  return (
    <DesktopOnly feature="Capture, hotkeys and launcher settings">
      <SettingsInner />
    </DesktopOnly>
  )
}

function SettingsInner() {
  const { settings, setSettings, toast, status } = useDesktop()
  const games = useGames()
  const [displays, setDisplays] = useState<{ id: number; label: string; primary: boolean; width: number; height: number }[]>([])

  useEffect(() => {
    bridge?.displays().then(setDisplays, () => {})
  }, [])

  if (!settings) return <div className="skeleton" style={{ height: 500 }} />

  async function save(patch: Partial<Settings>) {
    try {
      const next = await desktop.saveSettings(patch)
      setSettings(next)
      if (Object.keys(patch).some((k) => k.startsWith('hotkey_'))) bridge?.reloadHotkeys()
      if ('start_with_windows' in patch) bridge?.setLoginItem(Boolean(patch.start_with_windows))
    } catch (e) {
      toast({ title: 'Couldn’t save that', sub: (e as Error).message, tone: 'error' })
    }
  }

  const s = settings
  const approxMb = Math.round(((s.quality === 'ultra' ? 28 : s.quality === 'high' ? 16 : s.quality === 'medium' ? 9 : 5) * s.buffer_seconds * (s.fps / 60)) / 8)
  return (
    <div>
      <div className="page-head">
        <div>
          <div className="kicker bare">
            <b>05</b> Settings
          </div>
          <h1>
            Dial it <em>in</em>
          </h1>
        </div>
      </div>

      <div className="settings">
        <section className="card">
          <h2>Replay buffer</h2>
          <Row t="Clip length" h={`How far back a clip reaches. ~${approxMb} MB of disk while the buffer runs.`}>
            <input type="range" min={15} max={300} step={15} value={s.buffer_seconds} onChange={(e) => void save({ buffer_seconds: Number(e.target.value) })} />
            <span className="mono" style={{ width: 48, textAlign: 'right' }}>
              {s.buffer_seconds}s
            </span>
          </Row>
          <Row t="Frame rate">
            <div className="seg">
              {[30, 60, 120].map((f) => (
                <button key={f} aria-pressed={s.fps === f} onClick={() => void save({ fps: f })}>
                  {f} fps
                </button>
              ))}
            </div>
          </Row>
          <Row t="Quality" h="Higher quality means bigger files. High is visually lossless for most games.">
            <div className="seg">
              {(['low', 'medium', 'high', 'ultra'] as const).map((q) => (
                <button key={q} aria-pressed={s.quality === q} onClick={() => void save({ quality: q })}>
                  {q}
                </button>
              ))}
            </div>
          </Row>
          <Row t="Encoder" h={`Auto picks your GPU's hardware encoder.${status?.buffer.encoder ? ` Using ${status.buffer.encoder.toUpperCase()} right now.` : ''}`}>
            <label className="field" style={{ width: 200 }}>
              <select value={s.encoder} onChange={(e) => void save({ encoder: e.target.value as Settings['encoder'] })}>
                <option value="auto">Auto (recommended)</option>
                <option value="nvenc">NVIDIA NVENC</option>
                <option value="amf">AMD AMF</option>
                <option value="qsv">Intel Quick Sync</option>
                <option value="x264">CPU (x264)</option>
              </select>
            </label>
          </Row>
          <Row t="Monitor">
            <label className="field" style={{ width: 200 }}>
              <select value={s.monitor} onChange={(e) => void save({ monitor: Number(e.target.value) })}>
                {(displays.length ? displays : [{ id: 0, label: 'Primary display', primary: true, width: 0, height: 0 }]).map((d, i) => (
                  <option key={d.id} value={i}>
                    {d.primary ? '★ ' : ''}
                    {d.label || `Display ${i + 1}`}
                    {d.width ? ` · ${d.width}×${d.height}` : ''}
                  </option>
                ))}
              </select>
            </label>
          </Row>
          <Row t="Game audio" h="Everything you hear, captured with Windows loopback.">
            <Toggle label="Game audio" on={s.record_system_audio} onChange={(v) => void save({ record_system_audio: v })} />
          </Row>
          <Row t="Microphone" h="Mixed into the same track.">
            <Toggle label="Microphone" on={s.record_mic} onChange={(v) => void save({ record_mic: v })} />
          </Row>
          <Row t="Arm automatically" h="Start the buffer when a game from your library launches, stop it when you quit.">
            <Toggle label="Arm automatically" on={s.auto_buffer} onChange={(v) => void save({ auto_buffer: v })} />
          </Row>
        </section>

        <section className="card">
          <h2>Hotkeys</h2>
          <Row t="Save clip" h="Saves the last N seconds. Works in game.">
            <Hotkey value={s.hotkey_clip} onChange={(v) => void save({ hotkey_clip: v })} />
          </Row>
          <Row t="Start / stop recording">
            <Hotkey value={s.hotkey_record} onChange={(v) => void save({ hotkey_record: v })} />
          </Row>
          <Row t="Screenshot">
            <Hotkey value={s.hotkey_screenshot} onChange={(v) => void save({ hotkey_screenshot: v })} />
          </Row>
        </section>

        <section className="card">
          <h2>Storage</h2>
          <Row t="Clips folder" h={s.clips_dir}>
            {bridge && (
              <button
                className="btn"
                onClick={async () => {
                  const dir = await bridge?.pickFolder()
                  if (dir) void save({ clips_dir: dir })
                }}
              >
                Change…
              </button>
            )}
          </Row>
        </section>

        <section className="card">
          <h2>App</h2>
          <Row t="Start with Windows" h="Opens hidden in the tray so playtime and clips are always on.">
            <Toggle label="Start with Windows" on={s.start_with_windows} onChange={(v) => void save({ start_with_windows: v })} />
          </Row>
          <Row t="Close to tray" h="Closing the window keeps Clutch running.">
            <Toggle label="Close to tray" on={s.minimize_to_tray} onChange={(v) => void save({ minimize_to_tray: v })} />
          </Row>
          <Row t="Session recaps" h="A notification with playtime and clips when you close a game.">
            <Toggle label="Session recaps" on={s.notify_sessions} onChange={(v) => void save({ notify_sessions: v })} />
          </Row>
          <Row t="Refresh stats after games" h="Pull new matches for linked accounts when a session ends.">
            <Toggle label="Refresh stats after games" on={s.auto_sync_on_exit} onChange={(v) => void save({ auto_sync_on_exit: v })} />
          </Row>
        </section>

        <section className="card">
          <h2>Linked accounts</h2>
          {games.map((g) => {
            const key = s.linked_profiles[g.id]
            return (
              <Row key={g.id} t={g.name} h={key ? `Linked · ${key}` : g.configured ? 'Link from the game’s library page' : 'Needs an API key in backend/.env'}>
                {key ? (
                  <>
                    <Link className="btn small" to={`/${g.id}/p/${encodeURIComponent(key)}`}>
                      Open
                    </Link>
                    <button
                      className="btn small ghost"
                      onClick={() => {
                        const next = { ...s.linked_profiles }
                        delete next[g.id]
                        void save({ linked_profiles: next })
                      }}
                    >
                      Unlink
                    </button>
                  </>
                ) : (
                  <span className={`mono ${g.configured ? 'pill-ok' : 'pill-off'}`} style={{ fontSize: 11 }}>
                    {g.configured ? '● API ready' : '○ demo only'}
                  </span>
                )}
              </Row>
            )
          })}
        </section>
      </div>
    </div>
  )
}
