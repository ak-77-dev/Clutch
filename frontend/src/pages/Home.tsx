import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import { ClipTile } from '../components/ClipTile'
import { GameTile } from '../components/GameTile'
import { ClipIcon, PlayIcon } from '../components/Icons'
import { Portrait } from '../components/Portrait'
import { SearchBar } from '../components/SearchBar'
import { useDesktop } from '../components/Shell'
import { desktop, isDesktop, useDesktopEvents } from '../desktop'
import { fmtHours, hoursNumber, monogram } from '../format'
import { useAsync, useGames } from '../hooks'
import type { MatchSummary } from '../types'
import { useLaunch } from './Library'

const BLURB: Record<string, string> = {
  lol: 'Match history, champion pool, CS and vision trends from the official Riot API.',
  valorant: 'Agents, maps, ACS / ADR / HS% and rank history via HenrikDev.',
  rocketleague: 'Replays from ballchasing.com: boost, positioning, rank and duo chemistry.',
  dota2: 'Heroes, GPM / XPM, last hits and medal history from OpenDota. No key needed.',
  deadlock: 'Heroes, souls, accuracy and rank badges from deadlock-api.com. No key needed.',
  cod: 'K/D, SPM, damage and accuracy. Experimental: needs your own Activision token.',
}
const GLYPH: Record<string, string> = { lol: 'LOL', valorant: 'VAL', rocketleague: 'RL', dota2: 'D2', deadlock: 'DL', cod: 'COD' }

export function Home() {
  const { game } = useParams()
  return isDesktop && !game ? <Dashboard /> : <StatsHub />
}

// ── desktop dashboard ───────────────────────────────────────────────────────

function Dashboard() {
  const games = useGames()
  const { status, settings } = useDesktop()
  const launch = useLaunch()
  const navigate = useNavigate()
  const data = useAsync(async () => {
    const [lib, clips, pt] = await Promise.all([desktop.library(), desktop.clips(), desktop.playtime(14)])
    return { lib, clips: clips.items, pt }
  }, [])
  useDesktopEvents((e) => {
    if (e.type === 'clip_saved' || e.type === 'session_end' || e.type === 'game_started') data.reload()
  })
  const linked = Object.entries(settings?.linked_profiles ?? {})
  const ticker = useAsync(async () => {
    const lists = await Promise.all(
      linked.map(([g, key]) =>
        api.overview(g, key).then(
          (o) => o.recent.slice(0, 6).map((m) => ({ m, game: g })),
          () => [] as { m: MatchSummary; game: string }[],
        ),
      ),
    )
    return lists.flat().sort((a, b) => b.m.date.localeCompare(a.m.date))
  }, [linked.map(([g, k]) => g + k).join()])

  const lib = data.data?.lib ?? []
  const playing = status?.now_playing[0]
  const nowGame = playing ? lib.find((g) => g.id === playing.game_id) : undefined
  const recent = [...lib].filter((g) => g.last_played || g.running).sort((a, b) => Number(b.running) - Number(a.running) || (b.last_played ?? 0) - (a.last_played ?? 0)).slice(0, 10)
  const week = data.data?.pt.daily.slice(-7).reduce((s, d) => s + d.seconds, 0) ?? 0
  const weekClips = (data.data?.clips ?? []).filter((c) => c.created_at > Date.now() / 1000 - 7 * 86400).length
  const topWeek = [...(data.data?.pt.games ?? [])].sort((a, b) => b.week - a.week).filter((g) => g.week > 0).slice(0, 4)
  const today = new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })

  return (
    <div>
      {playing ? (
        <section className="now-card enter" style={{ margin: '0 0 22px' }}>
          {nowGame?.art.hero && <img className="bg" src={nowGame.art.hero} alt="" />}
          <div className="body">
            <div className="kicker bare" style={{ color: 'var(--up)' }}>
              <span className="rec-dot buffer" style={{ background: 'var(--up)', boxShadow: '0 0 10px var(--up)' }} /> Now playing
            </div>
            <h2>{playing.game_name}</h2>
            <div className="timer">
              {fmtHours(playing.seconds)} this session · {status?.buffer.active ? `replay buffer armed (${settings?.hotkey_clip ?? 'F8'} to clip)` : 'replay buffer off'}
            </div>
          </div>
          <div className="body row">
            <button className="btn primary" onClick={() => void desktop.clip().catch(() => {})}>
              <ClipIcon /> Clip it
            </button>
            <Link className="btn" to={`/library/${encodeURIComponent(playing.game_id)}`}>
              Game page
            </Link>
          </div>
        </section>
      ) : (
        <section className="hero">
          <div className="kicker bare">
            <b>//</b> {today}
          </div>
          <h1>
            Pick a game.
            <br />
            <em>Go clutch.</em>
          </h1>
          <p>
            {lib.length} games across your launchers, {fmtHours(week)} played this week. Hit {settings?.hotkey_clip ?? 'F8'} in game and the last{' '}
            {settings?.buffer_seconds ?? 60} seconds are yours.
          </p>
          {recent[0] && (
            <div className="cta">
              <button className="btn big primary" onClick={() => void launch(recent[0])}>
                <PlayIcon /> {recent[0].name}
              </button>
              <Link className="btn big" to="/library">
                Library
              </Link>
            </div>
          )}
        </section>
      )}

      {ticker.data && ticker.data.length > 0 && (
        <div className="ticker" aria-label="Recent matches">
          <div className="track">
            {[...ticker.data, ...ticker.data].map(({ m, game: g }, i) => (
              <span className="item" key={i}>
                <b style={{ color: games.find((x) => x.id === g)?.accent }}>{GLYPH[g] ?? g}</b>
                <span className={m.result === 'win' ? 'w' : 'l'}>{m.result === 'win' ? 'W' : m.result === 'loss' ? 'L' : 'D'}</span>
                {m.score_line} · {m.character}
                {m.map ? ` · ${m.map}` : ''}
              </span>
            ))}
          </div>
        </div>
      )}

      {recent.length > 0 && (
        <section style={{ marginBottom: 30 }}>
          <div className="kicker" style={{ marginBottom: 14 }}>
            <b>01</b> Jump back in
          </div>
          <div className="shelf">
            {recent.map((g) => (
              <GameTile key={g.id} game={g} onLaunch={(x) => void launch(x)} />
            ))}
          </div>
        </section>
      )}

      <div className="stat-row enter" style={{ marginBottom: 26 }}>
        <div className="stat hot">
          <div className="k">Played this week</div>
          <div className="v">
            {hoursNumber(week)}
            <small>h</small>
          </div>
        </div>
        <div className="stat">
          <div className="k">Clips this week</div>
          <div className="v">{weekClips}</div>
        </div>
        <div className="stat">
          <div className="k">Library</div>
          <div className="v">{lib.length}</div>
          <div className="d">installed games</div>
        </div>
        <div className="stat">
          <div className="k">Top this week</div>
          <div className="v" style={{ fontSize: 28, lineHeight: 1.05 }}>
            {topWeek[0]?.game_name ?? '—'}
          </div>
          <div className="d">{topWeek[0] ? fmtHours(topWeek[0].week) : 'play something'}</div>
        </div>
      </div>

      <div className="grid cols-3">
        <div className="stack">
          <div className="card">
            <h2>Your stats</h2>
            {linked.length === 0 ? (
              <p className="muted" style={{ margin: 0, fontSize: 13 }}>
                Link your accounts on a game’s library page (Valorant, League, Dota 2, Deadlock…) and your ranks show up here, refreshed after every session.
              </p>
            ) : (
              <div className="grid" style={{ gap: 8 }}>
                {linked.map(([g, key]) => {
                  const meta = games.find((x) => x.id === g)
                  return (
                    <Link key={g} className="row" to={`/${g}/p/${encodeURIComponent(key)}`} style={{ padding: '8px 0', borderBottom: '1px solid var(--line)' }}>
                      <span className="glyph" style={{ '--game': meta?.accent } as React.CSSProperties}>
                        {GLYPH[g] ?? monogram(g)}
                      </span>
                      <span style={{ fontWeight: 600 }}>{meta?.name ?? g}</span>
                      <span className="muted mono" style={{ marginLeft: 'auto', fontSize: 11 }}>
                        open →
                      </span>
                    </Link>
                  )
                })}
              </div>
            )}
          </div>
          {topWeek.length > 0 && (
            <div className="card">
              <h2>This week</h2>
              <div className="bars">
                {topWeek.map((g) => (
                  <Link key={g.game_id} to={`/library/${encodeURIComponent(g.game_id)}`} className="bar-row">
                    <img className="gicon" src={desktop.iconUrl(g.game_id)} alt="" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} />
                    <div style={{ minWidth: 0 }}>
                      <div className="name">{g.game_name}</div>
                      <div className="track">
                        <i style={{ width: `${(g.week / topWeek[0].week) * 100}%` }} />
                      </div>
                    </div>
                    <div className="val">{fmtHours(g.week)}</div>
                  </Link>
                ))}
              </div>
            </div>
          )}
        </div>
        <div className="card">
          <div className="spread" style={{ marginBottom: 14 }}>
            <h2 style={{ margin: 0 }}>Latest clips</h2>
            <Link className="btn small" to="/clips">
              Media hub
            </Link>
          </div>
          {(data.data?.clips.length ?? 0) === 0 ? (
            <div className="empty" style={{ padding: 24 }}>
              <span className="display" style={{ fontSize: 30 }}>
                Nothing clipped yet
              </span>
              Press {settings?.hotkey_clip ?? 'F8'} in any game.
            </div>
          ) : (
            <div className="clip-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))' }}>
              {data.data!.clips.slice(0, 6).map((c) => (
                <ClipTile key={c.id} clip={c} onOpen={() => navigate(`/clips?open=${c.id}`)} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── stats hub (player search) ───────────────────────────────────────────────

export function StatsHub() {
  const { game: focus } = useParams()
  const games = useGames()
  const navigate = useNavigate()
  const recent = useAsync(() => api.recent(), [])
  const [opening, setOpening] = useState<string | null>(null)

  async function openDemo(game: string) {
    setOpening(game)
    try {
      const p = await api.search(game, 'demo')
      navigate(`/${game}/p/${encodeURIComponent(p.key)}`)
    } finally {
      setOpening(null)
    }
  }

  const shown = focus ? games.filter((g) => g.id === focus) : games
  const meta = focus ? games.find((g) => g.id === focus) : undefined
  return (
    <div>
      <section className="hero" style={meta ? { background: `radial-gradient(60% 120% at 100% 0%, color-mix(in srgb, ${meta.accent} 22%, transparent), transparent 60%), var(--bg)` } : undefined}>
        <div className="kicker bare">
          <b>//</b> {meta ? `${meta.name} · player search` : `${games.length} games · player search`}
        </div>
        <h1>
          {meta ? (
            <>
              {meta.name}
              <br />
              <em style={{ color: meta.accent }}>scouting.</em>
            </>
          ) : (
            <>
              Scout any
              <br />
              <em>player.</em>
            </>
          )}
        </h1>
        <p>Match history, rank climb, and plain-English reads on what separates their wins from their losses.</p>
        <div className="cta" style={{ maxWidth: 640 }}>
          <SearchBar game={focus} />
        </div>
      </section>

      <div className="game-cards">
        {shown.map((g) => (
          <div className="game-card enter" key={g.id} data-glyph={GLYPH[g.id] ?? monogram(g.name)} style={{ '--game': g.accent } as React.CSSProperties}>
            <h3>{g.name}</h3>
            <p className="muted" style={{ margin: 0, position: 'relative', fontSize: 13 }}>
              {BLURB[g.id]}
            </p>
            <div className={`status ${g.configured ? 'pill-ok' : 'pill-off'}`} style={{ position: 'relative' }}>
              {g.configured ? '● Live data' : '○ Demo data (no API key)'}
            </div>
            <div className="row">
              <button className="btn primary" style={{ background: g.accent, borderColor: g.accent }} onClick={() => void openDemo(g.id)} disabled={opening === g.id}>
                {opening === g.id ? 'Loading…' : 'Demo profile'}
              </button>
              {!focus && (
                <Link className="btn" to={`/${g.id}`}>
                  Search
                </Link>
              )}
            </div>
          </div>
        ))}
      </div>

      {recent.data && recent.data.length > 0 && (
        <section style={{ marginTop: 30 }}>
          <div className="kicker" style={{ marginBottom: 12 }}>
            <b>//</b> Recently viewed
          </div>
          <div className="recent-list">
            {recent.data.map((p) => (
              <Link key={`${p.game}:${p.key}`} to={`/${p.game}/p/${encodeURIComponent(p.key)}`}>
                <Portrait src={p.icon} name={p.name} size="xs" />
                <span>
                  {p.name}
                  {p.tag ? <span className="muted">#{p.tag}</span> : null}
                </span>
                <span className="muted mono" style={{ fontSize: 10.5 }}>
                  {games.find((g) => g.id === p.game)?.name}
                </span>
              </Link>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
