import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Hotkey, Toggle } from '../components/Controls'
import { useDesktop } from '../components/Shell'
import { bridge, desktop, type ApiKeys, type AutoClipGame, type Capabilities, type Settings, type StorageInfo } from '../desktop'
import { useGames, useReloadGames } from '../hooks'
import { estimateSize, fmtBytes } from '../format'

const LOL_PLATFORMS = ['na1', 'euw1', 'eun1', 'kr', 'br1', 'la1', 'la2', 'oc1', 'tr1', 'ru', 'jp1', 'me1', 'ph2', 'sg2', 'th2', 'tw2', 'vn2']
// Settings the Electron shell reads itself (global hotkeys, the in-game panel, Discord).
const SHELL_KEYS = ['hotkey_', 'overlay_', 'discord_']

/** A text setting that saves when you leave the field or press Enter. */
function TextSetting({ value, onSave, placeholder, secret = false, width = 260 }: { value: string; onSave: (v: string) => void; placeholder?: string; secret?: boolean; width?: number }) {
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])
  return (
    <label className="field" style={{ width }}>
      <input
        type={secret ? 'password' : 'text'}
        value={draft}
        placeholder={placeholder}
        spellCheck={false}
        autoComplete="off"
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => draft.trim() !== value && onSave(draft.trim())}
        onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
      />
    </label>
  )
}

/** Riot / HenrikDev / ballchasing keys, written to the .env next to Clutch's database. */
function ApiKeysCard() {
  const { toast } = useDesktop()
  const reloadGames = useReloadGames()
  const [keys, setKeys] = useState<ApiKeys | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  useEffect(() => {
    desktop.keys().then(setKeys, () => {})
  }, [])

  async function save(values: Record<string, string | null>, what: string) {
    try {
      setKeys(await desktop.saveKeys(values))
      setDrafts({})
      reloadGames()
      toast({ title: what })
    } catch (e) {
      toast({ title: 'Couldn’t save the key', sub: (e as Error).message, tone: 'error' })
    }
  }

  if (!keys) return null
  return (
    <section className="card">
      <h2>API keys</h2>
      <p className="sub">Games with a key show live stats; without one they show demo data. Dota 2 and Deadlock don’t need one. The CoD token is your ACT_SSO_COOKIE from callofduty.com (experimental).</p>
      {keys.keys.map((k) => (
        <Row key={k.name} t={k.label} h={k.set ? `Saved · ${k.preview}` : `Not set · get one at ${k.help.replace('https://', '')}`}>
          <label className="field" style={{ width: 230 }}>
            <input
              type="password"
              value={drafts[k.name] ?? ''}
              placeholder={k.set ? 'Replace key…' : 'Paste key'}
              spellCheck={false}
              autoComplete="off"
              onChange={(e) => setDrafts((d) => ({ ...d, [k.name]: e.target.value }))}
              onKeyDown={(e) => e.key === 'Enter' && drafts[k.name]?.trim() && void save({ [k.name]: drafts[k.name].trim() }, `${k.label} saved`)}
            />
          </label>
          <button className="btn small" disabled={!drafts[k.name]?.trim()} onClick={() => void save({ [k.name]: drafts[k.name].trim() }, `${k.label} saved`)}>
            Save
          </button>
          {k.set && (
            <button className="btn small ghost" onClick={() => window.confirm(`Remove the ${k.label}?`) && void save({ [k.name]: null }, `${k.label} removed`)}>
              Remove
            </button>
          )}
          <button className="btn small ghost" title={k.help} onClick={() => bridge?.openExternal?.(k.help)}>
            Get one ↗
          </button>
        </Row>
      ))}
      <Row t="League region" h="Where your League account plays.">
        <label className="field" style={{ width: 120 }}>
          <select value={keys.lol_platform} onChange={(e) => void save({ LOL_PLATFORM: e.target.value }, 'Region saved')}>
            {LOL_PLATFORMS.map((p) => (
              <option key={p} value={p}>
                {p.toUpperCase()}
              </option>
            ))}
          </select>
        </label>
      </Row>
      <p className="mono muted" style={{ margin: '10px 0 0', fontSize: 10.5 }}>
        Stored on this PC only · {keys.path}
      </p>
    </section>
  )
}

function AutoClipCard({ s, save }: { s: Settings; save: (p: Partial<Settings>) => Promise<void> }) {
  const { toast } = useDesktop()
  const [games, setGames] = useState<AutoClipGame[]>([])
  const load = () => desktop.autoclip().then(setGames, () => {})
  useEffect(() => {
    void load()
  }, [])
  return (
    <section className="card">
      <h2>Auto-clip highlights</h2>
      <Row t="Save highlights automatically" h="Clutch listens to the game’s own live data and clips kills for you, a few seconds after the moment. Needs the replay buffer on.">
        <Toggle label="Auto-clip" on={s.auto_clip} onChange={(v) => void save({ auto_clip: v })} />
      </Row>
      {s.auto_clip && (
        <>
          <Row t="Clip on" h="The smallest moment worth a clip: anything that big or bigger gets saved.">
            <div className="seg">
              {(['kill', 'multikill', 'ace'] as const).map((k) => (
                <button key={k} aria-pressed={s.auto_clip_min === k} onClick={() => void save({ auto_clip_min: k })}>
                  {k === 'kill' ? 'Every kill' : k === 'multikill' ? 'Multikills' : 'Aces / pentas'}
                </button>
              ))}
            </div>
          </Row>
          {games.map((g) => (
            <Row key={g.game_id} t={g.name} h={g.method}>
              {!g.needs_install || g.installed ? (
                <span className="mono pill-ok" style={{ fontSize: 11 }}>
                  ● ready
                </span>
              ) : (
                <button
                  className="btn small"
                  onClick={() =>
                    void desktop.installAutoclip(g.game_id).then(
                      (r) => {
                        toast({ title: `${g.name} connected`, sub: `Restart the game once · ${r.path}` })
                        void load()
                      },
                      (e: Error) => toast({ title: 'Couldn’t set it up', sub: e.message, tone: 'error' }),
                    )
                  }
                >
                  Connect
                </button>
              )}
            </Row>
          ))}
          <p className="hint" style={{ margin: '8px 0 0' }}>
            Valorant, Rocket League and CoD don’t publish live kill data, so use the clip hotkey there.
          </p>
        </>
      )}
    </section>
  )
}

function StorageCard({ s, save }: { s: Settings; save: (p: Partial<Settings>) => Promise<void> }) {
  const { toast } = useDesktop()
  const [info, setInfo] = useState<StorageInfo | null>(null)
  const [cleaning, setCleaning] = useState(false)
  useEffect(() => {
    desktop.storage().then(setInfo, () => {})
  }, [s.storage_max_days, s.storage_max_gb])
  const plan = info?.plan
  return (
    <>
      <Row t="Delete old clips" h="Older clips go to the Recycle Bin. Favorites are always kept.">
        <div className="seg">
          {[0, 7, 30, 90].map((d) => (
            <button key={d} aria-pressed={s.storage_max_days === d} onClick={() => void save({ storage_max_days: d })}>
              {d ? `${d} days` : 'Never'}
            </button>
          ))}
        </div>
      </Row>
      <Row t="Size limit" h="When clips pass this size, the oldest non-favorites go first.">
        <div className="seg">
          {[0, 25, 50, 100, 250].map((g) => (
            <button key={g} aria-pressed={s.storage_max_gb === g} onClick={() => void save({ storage_max_gb: g })}>
              {g ? `${g} GB` : 'None'}
            </button>
          ))}
        </div>
      </Row>
      {info && (
        <div className="storage-meter">
          <div className="spread">
            <span className="mono" style={{ fontSize: 12 }}>
              {info.count} files · {fmtBytes(info.bytes)}
              {s.storage_max_gb ? ` of ${s.storage_max_gb} GB` : ''}
            </span>
            {plan && plan.count > 0 ? (
              <button
                className="btn small danger"
                disabled={cleaning}
                onClick={async () => {
                  setCleaning(true)
                  try {
                    const r = await desktop.cleanStorage()
                    toast({ title: `Cleaned up ${r.count} file${r.count === 1 ? '' : 's'}`, sub: `${fmtBytes(r.bytes)} freed · in the Recycle Bin` })
                    setInfo(await desktop.storage())
                  } finally {
                    setCleaning(false)
                  }
                }}
              >
                {cleaning ? 'Cleaning…' : `Clean ${plan.count} now (${fmtBytes(plan.bytes)})`}
              </button>
            ) : (
              <span className="mono muted" style={{ fontSize: 11 }}>
                {s.storage_max_days || s.storage_max_gb ? 'nothing to clean' : 'no limits set'}
              </span>
            )}
          </div>
          {s.storage_max_gb > 0 && (
            <div className={`goal-bar ${info.bytes > s.storage_max_gb * 1e9 ? 'over' : ''}`}>
              <i style={{ width: `${Math.min(100, (info.bytes / (s.storage_max_gb * 1e9)) * 100)}%` }} />
            </div>
          )}
        </div>
      )}
    </>
  )
}
import { DesktopOnly } from './DesktopOnly'

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
      if (Object.keys(patch).some((k) => SHELL_KEYS.some((prefix) => k.startsWith(prefix)))) bridge?.reloadHotkeys()
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
      <div className="page-head" data-echo="settings">
        <div>
          <div className="kicker bare">
            <b>08</b> Settings
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
          <Row t="Microphone" h={s.separate_audio_tracks ? 'Mixed into the main track, and also saved on its own.' : 'Mixed into the same track.'}>
            <Toggle label="Microphone" on={s.record_mic} onChange={(v) => void save({ record_mic: v })} />
          </Row>
          {s.record_mic && (
            <Row t="Separate audio tracks" h="Clips also get game-only and mic-only tracks, so you can export a copy without your voice. Editors like Premiere and DaVinci see all three.">
              <Toggle label="Separate audio tracks" on={s.separate_audio_tracks} onChange={(v) => void save({ separate_audio_tracks: v })} />
            </Row>
          )}
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
          <Row t="Show / hide session panel" h="The in-game panel (see below).">
            <Hotkey value={s.hotkey_overlay} onChange={(v) => void save({ hotkey_overlay: v })} />
          </Row>
        </section>

        <AutoClipCard s={s} save={save} />

        <section className="card">
          <h2>In game</h2>
          <Row t="Session panel" h="A small click-through panel in the corner: session time, today’s W/L, clips and your daily cap. Works over borderless-windowed games.">
            <Toggle label="Session panel" on={s.overlay_enabled} onChange={(v) => void save({ overlay_enabled: v })} />
          </Row>
          <Row t="Discord Rich Presence" h="Shows your session time, rank and today’s record on your Discord profile.">
            <Toggle label="Discord Rich Presence" on={s.discord_rpc} onChange={(v) => void save({ discord_rpc: v })} />
          </Row>
          {s.discord_rpc && (
            <Row
              t="Discord application ID"
              h="Create an application at discord.com/developers (free) and paste its Application ID. Its name is what Discord shows after “Playing”, so call it Clutch."
            >
              <TextSetting value={s.discord_client_id} placeholder="e.g. 1234567890123456789" onSave={(v) => void save({ discord_client_id: v })} width={230} />
              <button className="btn small ghost" onClick={() => bridge?.openExternal?.('https://discord.com/developers/applications')}>
                Open ↗
              </button>
            </Row>
          )}
        </section>

        <section className="card">
          <h2>Sharing</h2>
          <Row t="Share links upload to" h="Uploads are public: anyone with the link can watch. Clutch asks before every upload.">
            <div className="seg">
              <button aria-pressed={s.share_host === 'catbox'} onClick={() => void save({ share_host: 'catbox' })}>
                catbox · permanent
              </button>
              <button aria-pressed={s.share_host === 'litterbox'} onClick={() => void save({ share_host: 'litterbox' })}>
                litterbox · 72 h
              </button>
            </div>
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
          <StorageCard s={s} save={save} />
        </section>

        <ApiKeysCard />

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
          <Row t="Refresh stats after games" h="Pull new matches for linked accounts when a session ends. Also fills in the session’s report card.">
            <Toggle label="Refresh stats after games" on={s.auto_sync_on_exit} onChange={(v) => void save({ auto_sync_on_exit: v })} />
          </Row>
          <Row t="SteamGridDB key" h="Optional: cover art for games outside Steam (Epic, Battle.net, custom). Free at steamgriddb.com → Preferences → API.">
            <TextSetting value={s.steamgriddb_key} secret placeholder="Paste key" onSave={(v) => void save({ steamgriddb_key: v })} width={230} />
          </Row>
          <Row t="First-run setup" h="Walk through hotkeys, clip length and accounts again.">
            <button className="btn small" onClick={() => void save({ onboarded: false })}>
              Run again
            </button>
          </Row>
          <Row t="Version" h={bridge?.packaged ? 'Updates download in the background and install when you restart.' : 'Development build: updates are off.'}>
            <span className="mono" style={{ fontSize: 12 }}>
              {bridge?.version ?? 'web'}
            </span>
            {bridge?.packaged && (
              <button className="btn small" onClick={() => bridge?.checkUpdates?.()}>
                Check now
              </button>
            )}
          </Row>
        </section>

        <section className="card">
          <h2>Linked accounts</h2>
          {games.map((g) => {
            const key = s.linked_profiles[g.id]
            return (
              <Row key={g.id} t={g.name} h={key ? `Linked · ${key}` : g.configured ? 'Link from the game’s library page' : 'Needs an API key (see API keys above)'}>
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
