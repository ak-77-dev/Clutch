import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useDesktop } from '../components/Shell'
import { bridge, desktop, type Capabilities, type Settings } from '../desktop'
import { useGames } from '../hooks'
import { accelerator, estimateSize } from '../format'
import { DesktopOnly } from './DesktopOnly'

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
  const [caps, setCaps] = useState<Capabilities | null>(null)

  useEffect(() => {
    bridge?.displays().then(setDisplays, () => {})
    desktop.capabilities().then(setCaps, () => {})
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
  const codecAvailable = (c: string) => Object.values(caps?.encoders ?? {}).some((codecs) => codecs[c])
  const display = displays.find((d) => d.primary) ?? displays[0]
  const estimate = estimateSize(s, display?.height ?? 1080)
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
          <Row t="Clip length" h="How far back a clip reaches when you press the clip key.">
            <input type="range" min={15} max={300} step={15} value={s.buffer_seconds} onChange={(e) => void save({ buffer_seconds: Number(e.target.value) })} />
            <span className="mono" style={{ width: 48, textAlign: 'right' }}>
              {s.buffer_seconds}s
            </span>
          </Row>
          <Row t="Arm automatically" h="Start the buffer when a game from your library launches, stop it when you quit.">
            <Toggle label="Arm automatically" on={s.auto_buffer} onChange={(v) => void save({ auto_buffer: v })} />
          </Row>
        </section>

        <section className="card">
          <div className="spread" style={{ marginBottom: 6 }}>
            <h2 style={{ margin: 0 }}>Video</h2>
            <span className="mono" style={{ fontSize: 11.5, color: 'var(--volt)' }} title="Rough size for fast-moving gameplay">
              ≈ {estimate.mbps} Mbps · {estimate.perClip} per {s.buffer_seconds}s clip
            </span>
          </div>
          <Row t="Codec" h="H.264 plays everywhere (Discord, phones). HEVC is the same quality at about half the size. AV1 needs an RTX 40, RX 7000 or Intel Arc GPU.">
            <div className="seg">
              {(['h264', 'hevc', 'av1'] as const).map((c) => (
                <button key={c} aria-pressed={s.codec === c} disabled={caps ? !codecAvailable(c) : false} title={caps && !codecAvailable(c) ? 'Your GPU can’t encode this' : undefined} onClick={() => void save({ codec: c })}>
                  {c === 'h264' ? 'H.264' : c === 'hevc' ? 'HEVC' : 'AV1'}
                </button>
              ))}
            </div>
          </Row>
          <Row t="Resolution" h="Native records your monitor as-is. Lower resolutions make smaller files (scaled on the CPU).">
            <div className="seg">
              {(['native', '1440', '1080', '720'] as const).map((r) => (
                <button key={r} aria-pressed={s.resolution === r} onClick={() => void save({ resolution: r })}>
                  {r === 'native' ? 'Native' : `${r}p`}
                </button>
              ))}
            </div>
          </Row>
          <Row t="Frame rate">
            <div className="seg">
              {[30, 60, 120, 144].map((f) => (
                <button key={f} aria-pressed={s.fps === f} onClick={() => void save({ fps: f })}>
                  {f}
                </button>
              ))}
            </div>
          </Row>
          <Row t="Quality mode" h="Constant quality looks consistent and only spends bits where the action is. Target bitrate gives predictable file sizes.">
            <div className="seg">
              <button aria-pressed={s.rate_control === 'quality'} onClick={() => void save({ rate_control: 'quality' })}>
                Constant quality
              </button>
              <button aria-pressed={s.rate_control === 'bitrate'} onClick={() => void save({ rate_control: 'bitrate' })}>
                Target bitrate
              </button>
            </div>
          </Row>
          {s.rate_control === 'quality' ? (
            <Row t="Quality" h="High is visually lossless for most games. Max is for editing footage later.">
              <div className="seg">
                {(['low', 'medium', 'high', 'ultra', 'max'] as const).map((q) => (
                  <button key={q} aria-pressed={s.quality === q} onClick={() => void save({ quality: q })}>
                    {q}
                  </button>
                ))}
              </div>
            </Row>
          ) : (
            <Row t="Bitrate" h="YouTube recommends 12 Mbps for 1080p60 uploads; 40+ keeps fast games crisp for editing.">
              <input type="range" min={5} max={150} step={5} value={s.bitrate_mbps} onChange={(e) => void save({ bitrate_mbps: Number(e.target.value) })} />
              <span className="mono" style={{ width: 70, textAlign: 'right' }}>
                {s.bitrate_mbps} Mbps
              </span>
            </Row>
          )}
          <Row t="Encoder effort" h="Quality uses the GPU encoder's slowest preset with multipass: sharper motion, a bit more GPU. Balanced is right for most PCs.">
            <div className="seg">
              {(['speed', 'balanced', 'quality'] as const).map((p) => (
                <button key={p} aria-pressed={s.preset === p} onClick={() => void save({ preset: p })}>
                  {p}
                </button>
              ))}
            </div>
          </Row>
          <Row t="Encoder" h={`Auto picks your GPU's hardware encoder.${status?.buffer.encoder ? ` Using ${status.buffer.encoder.toUpperCase()} ${status.buffer.codec?.toUpperCase() ?? ''} right now.` : ''}`}>
            <label className="field" style={{ width: 220 }}>
              <select value={s.encoder} onChange={(e) => void save({ encoder: e.target.value as Settings['encoder'] })}>
                <option value="auto">Auto (recommended)</option>
                {(['nvenc', 'amf', 'qsv', 'x264'] as const).map((v) => (
                  <option key={v} value={v} disabled={caps ? !caps.encoders[v]?.[s.codec] : false}>
                    {{ nvenc: 'NVIDIA NVENC', amf: 'AMD AMF', qsv: 'Intel Quick Sync', x264: 'CPU (x264 / x265)' }[v]}
                    {caps && !caps.encoders[v]?.[s.codec] ? ' — unavailable' : ''}
                  </option>
                ))}
              </select>
            </label>
          </Row>
          <Row t="Monitor">
            <label className="field" style={{ width: 220 }}>
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
        </section>

        <section className="card">
          <h2>Audio</h2>
          <Row t="Game audio" h="Everything you hear. Follows Windows' default device, including when a Bluetooth headset switches modes for a mic.">
            <Toggle label="Game audio" on={s.record_system_audio} onChange={(v) => void save({ record_system_audio: v })} />
          </Row>
          {s.record_system_audio && (
            <Row t="Output device">
              <label className="field" style={{ width: 260 }}>
                <select value={s.audio_device} onChange={(e) => void save({ audio_device: e.target.value })}>
                  <option value="default">Follow Windows default</option>
                  {caps?.audio.outputs.map((d) => (
                    <option key={d} value={d}>
                      {d}
                    </option>
                  ))}
                </select>
              </label>
            </Row>
          )}
          <Row t="Microphone" h="Mixed into the same track.">
            <Toggle label="Microphone" on={s.record_mic} onChange={(v) => void save({ record_mic: v })} />
          </Row>
          {s.record_mic && (
            <Row t="Mic device">
              <label className="field" style={{ width: 260 }}>
                <select value={s.mic_device} onChange={(e) => void save({ mic_device: e.target.value })}>
                  <option value="default">Follow Windows default</option>
                  {caps?.audio.inputs.map((d) => (
                    <option key={d} value={d}>
                      {d}
                    </option>
                  ))}
                </select>
              </label>
            </Row>
          )}
          <Row t="Audio quality">
            <div className="seg">
              {[128, 192, 256, 320].map((k) => (
                <button key={k} aria-pressed={s.audio_kbps === k} onClick={() => void save({ audio_kbps: k })}>
                  {k} kbps
                </button>
              ))}
            </div>
          </Row>
          {status?.buffer.active && (
            <p className="mono muted" style={{ margin: '10px 0 0', fontSize: 11 }}>
              Capturing: {status.buffer.audio.length ? status.buffer.audio.join(' · ') : 'no audio'}
              {status.buffer.audio_restarts ? ` · reconnected ${status.buffer.audio_restarts}×` : ''}
            </p>
          )}
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
