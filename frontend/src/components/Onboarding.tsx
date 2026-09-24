import { useState } from 'react'
import { api } from '../api'
import { bridge, desktop, type Settings } from '../desktop'
import { useGames } from '../hooks'
import { Hotkey, Toggle } from './Controls'
import { CheckIcon, ChevronLeft, ChevronRight } from './Icons'
import { useDesktop, Wordmark } from './Shell'

const STEPS = ['Welcome', 'Clipping', 'Quality', 'Accounts', 'Extras'] as const

const QUALITY: { id: string; label: string; blurb: string; patch: Partial<Settings> }[] = [
  { id: 'light', label: 'Light', blurb: '1080p · 60 fps · small files. Best for older GPUs and laptops.', patch: { quality: 'medium', resolution: '1080', fps: 60, preset: 'speed' } },
  { id: 'balanced', label: 'Balanced', blurb: 'Native resolution · 60 fps · visually lossless. Right for most PCs.', patch: { quality: 'high', resolution: 'native', fps: 60, preset: 'balanced' } },
  { id: 'creator', label: 'Creator', blurb: 'Native · 60 fps · near-lossless on the slowest GPU preset. Big files, made for editing.', patch: { quality: 'ultra', resolution: 'native', fps: 60, preset: 'quality' } },
]

const EXTRAS = [
  { k: 'auto_clip', t: 'Auto-clip highlights', h: 'Kills and multikills in League, Dota 2 and CS2 get clipped for you.' },
  { k: 'overlay_enabled', t: 'In-game session panel', h: 'Session time, today’s record and your daily cap in the corner of the screen.' },
  { k: 'start_with_windows', t: 'Start with Windows', h: 'Opens in the tray so playtime and the replay buffer are always ready.' },
  { k: 'notify_sessions', t: 'Session recaps', h: 'A notification with your playtime and clips when you close a game.' },
] as const

function Setting({ t, h, children }: { t: string; h?: string; children: React.ReactNode }) {
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

/** First-run setup: hotkeys, clip length, quality, accounts, extras. Shown until `onboarded` is set. */
export function Onboarding() {
  const { settings, setSettings, toast } = useDesktop()
  const games = useGames()
  const [step, setStep] = useState(0)
  const [queries, setQueries] = useState<Record<string, string>>({})
  const [linking, setLinking] = useState<string | null>(null)
  if (!settings || settings.onboarded) return null
  const s = settings

  async function save(patch: Partial<Settings>) {
    try {
      setSettings(await desktop.saveSettings(patch))
      if (Object.keys(patch).some((k) => k.startsWith('hotkey_') || k.startsWith('overlay_'))) bridge?.reloadHotkeys()
      if ('start_with_windows' in patch) bridge?.setLoginItem(Boolean(patch.start_with_windows))
    } catch (e) {
      toast({ title: 'Couldn’t save that', sub: (e as Error).message, tone: 'error' })
    }
  }

  async function link(game: string) {
    const q = queries[game]?.trim()
    if (!q) return
    setLinking(game)
    try {
      const profile = await api.search(game, q)
      await save({ linked_profiles: { ...s.linked_profiles, [game]: profile.key } })
      toast({ title: `Linked ${profile.name}` })
    } catch (e) {
      toast({ title: 'Couldn’t find that player', sub: (e as Error).message, tone: 'error' })
    } finally {
      setLinking(null)
    }
  }

  const quality = QUALITY.find((q) => Object.entries(q.patch).every(([k, v]) => s[k as keyof Settings] === v))?.id
  const last = step === STEPS.length - 1
  return (
    <div className="onboard" role="dialog" aria-modal="true" aria-label="Set up Clutch">
      <div className="onboard-card">
        <div className="onboard-top">
          <Wordmark />
          <ol className="steps">
            {STEPS.map((name, i) => (
              <li key={name} className={i === step ? 'on' : i < step ? 'done' : ''}>
                <span>{i < step ? <CheckIcon /> : String(i + 1).padStart(2, '0')}</span>
                {name}
              </li>
            ))}
          </ol>
        </div>

        <div className="onboard-body" key={step}>
          {step === 0 && (
            <>
              <div className="kicker bare">
                <b>//</b> Setup · 1 minute
              </div>
              <h1>
                Every clutch,
                <br />
                <em>on tape.</em>
              </h1>
              <p className="lead">
                Clutch runs quietly in the tray. When a game starts, it tracks your playtime and keeps a rolling replay buffer on your GPU. Hit one key and the last few seconds are saved,
                with audio. Your stats, clips and sessions stay on this PC.
              </p>
              <ul className="ticks">
                <li>Replay buffer with GPU encoding (NVENC, AMF, Quick Sync)</li>
                <li>Auto-clips kills in League, Dota 2 and CS2</li>
                <li>Every launcher’s games in one library, with playtime</li>
                <li>Stats for League, Valorant, Rocket League, Dota 2, Deadlock and CoD</li>
              </ul>
            </>
          )}

          {step === 1 && (
            <>
              <h2 className="display">Clip the last…</h2>
              <div className="big-choice">
                {[15, 30, 60, 120].map((sec) => (
                  <button key={sec} aria-pressed={s.buffer_seconds === sec} onClick={() => void save({ buffer_seconds: sec })}>
                    <b>{sec < 60 ? sec : sec / 60}</b>
                    {sec < 60 ? 'seconds' : sec === 60 ? 'minute' : 'minutes'}
                  </button>
                ))}
              </div>
              <Setting t="Save clip" h="Works inside games. Pick something you won’t hit by accident.">
                <Hotkey value={s.hotkey_clip} onChange={(v) => void save({ hotkey_clip: v })} />
              </Setting>
              <Setting t="Start / stop recording">
                <Hotkey value={s.hotkey_record} onChange={(v) => void save({ hotkey_record: v })} />
              </Setting>
              <Setting t="Screenshot">
                <Hotkey value={s.hotkey_screenshot} onChange={(v) => void save({ hotkey_screenshot: v })} />
              </Setting>
            </>
          )}

          {step === 2 && (
            <>
              <h2 className="display">How should it look?</h2>
              <div className="quality-pick">
                {QUALITY.map((q) => (
                  <button key={q.id} aria-pressed={quality === q.id} onClick={() => void save(q.patch)}>
                    <b>{q.label}</b>
                    <span>{q.blurb}</span>
                  </button>
                ))}
              </div>
              <Setting t="Record my microphone" h="Kept on its own track too, so you can export a copy without your voice.">
                <Toggle label="Microphone" on={s.record_mic} onChange={(v) => void save(v ? { record_mic: true, separate_audio_tracks: true } : { record_mic: false })} />
              </Setting>
              <p className="hint">Codec, bitrate, monitor and audio devices are in Settings.</p>
            </>
          )}

          {step === 3 && (
            <>
              <h2 className="display">Link your accounts</h2>
              <p className="lead" style={{ marginTop: 0 }}>
                Optional. Linked accounts refresh after every session, fill in your report cards and put your rank in the in-game panel.
              </p>
              <div className="link-list">
                {games.map((g) => (
                  <div key={g.id} className="link-row" style={{ '--game': g.accent } as React.CSSProperties}>
                    <span className="name">{g.name}</span>
                    {s.linked_profiles[g.id] ? (
                      <span className="mono pill-ok" style={{ fontSize: 11 }}>
                        ● linked
                      </span>
                    ) : g.configured ? (
                      <>
                        <label className="field">
                          <input
                            value={queries[g.id] ?? ''}
                            placeholder={g.search_hint}
                            aria-label={`${g.name} account`}
                            onChange={(e) => setQueries((q) => ({ ...q, [g.id]: e.target.value }))}
                            onKeyDown={(e) => e.key === 'Enter' && void link(g.id)}
                          />
                        </label>
                        <button className="btn small" disabled={!queries[g.id]?.trim() || linking !== null} onClick={() => void link(g.id)}>
                          {linking === g.id ? 'Finding…' : 'Link'}
                        </button>
                      </>
                    ) : (
                      <span className="mono muted" style={{ fontSize: 11 }}>
                        needs an API key · Settings → API keys
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}

          {step === 4 && (
            <>
              <h2 className="display">Last touches</h2>
              {EXTRAS.map((row) => (
                <Setting key={row.k} t={row.t} h={row.k === 'overlay_enabled' ? `${row.h} ${s.hotkey_overlay} toggles it.` : row.h}>
                  <Toggle label={row.t} on={Boolean(s[row.k])} onChange={(v) => void save({ [row.k]: v })} />
                </Setting>
              ))}
            </>
          )}
        </div>

        <div className="onboard-foot">
          {step > 0 ? (
            <button className="btn ghost" onClick={() => setStep(step - 1)}>
              <ChevronLeft /> Back
            </button>
          ) : (
            <button className="btn ghost" onClick={() => void save({ onboarded: true })}>
              Skip setup
            </button>
          )}
          <span className="mono muted" style={{ fontSize: 11 }}>
            {step + 1} / {STEPS.length}
          </span>
          <button className="btn primary" onClick={() => (last ? void save({ onboarded: true }) : setStep(step + 1))}>
            {last ? (
              <>
                <CheckIcon /> Start clutching
              </>
            ) : (
              <>
                {step === 0 ? 'Set up' : 'Next'} <ChevronRight />
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
