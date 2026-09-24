import { useEffect, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { FactorsChart, FormChart, RankChart, TiltChart, TrendChart } from '../components/Charts'
import { MatchCard, MatchClipsContext } from '../components/MatchCard'
import { useDesktop } from '../components/Shell'
import { desktop, isDesktop } from '../desktop'
import { Portrait } from '../components/Portrait'
import { CharacterTable, Insights, RankCard, SimpleGroupTable, StatTiles } from '../components/ProfileBits'
import { fmtWinRate, plural, timeAgo } from '../format'
import { useAsync } from '../hooks'
import type { Game, MatchPage, OverviewResponse } from '../types'

type Tab = 'overview' | 'matches' | 'pool' | 'insights'

export function ProfilePage() {
  const { game = '', key = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const tab = (params.get('tab') as Tab) || 'overview'
  const data = useAsync(() => api.overview(game, key), [game, key])
  const { settings } = useDesktop()
  const own = isDesktop && settings?.linked_profiles[game] === key
  const clips = useAsync(() => (own ? desktop.matchClips(game, key).catch(() => ({})) : Promise.resolve({})), [game, key, own])
  const [syncing, setSyncing] = useState(false)
  const [syncMsg, setSyncMsg] = useState<string | null>(null)

  const setTab = (t: Tab, extra: Record<string, string> = {}) => setParams({ ...(t === 'overview' ? {} : { tab: t }), ...extra }, { replace: true })

  if (data.loading && !data.data) return <ProfileSkeleton />
  if (data.error) return <div className="card empty error">{data.error.message}</div>
  const r = data.data as OverviewResponse
  const g = r.game
  const p = r.profile
  const ov = r.overview

  async function refresh() {
    setSyncing(true)
    setSyncMsg(null)
    try {
      const res = await api.sync(game, key)
      setSyncMsg(res.demo ? 'Demo profiles don’t sync.' : res.new ? `${res.new} new matches` : 'Up to date')
      data.reload()
    } catch (e) {
      setSyncMsg((e as Error).message)
    } finally {
      setSyncing(false)
    }
  }

  const characterFilter = params.get('character') ?? undefined
  return (
    <MatchClipsContext.Provider value={clips.data ?? {}}>
    <div style={{ '--accent': g.accent } as React.CSSProperties}>
      <header className="profile-head">
        <div className="avatar">
          {p.icon ? <img src={p.icon} alt="" /> : p.name.slice(0, 1).toUpperCase()}
          {p.level != null && <span className="lvl">Lv {p.level}</span>}
        </div>
        <div>
          <h1>
            {p.name}
            {p.tag && <span className="tag">#{p.tag}</span>}
          </h1>
          <div className="chips">
            <span className="chip">{g.name}</span>
            {p.region && <span className="chip">{p.region.toUpperCase()}</span>}
            <span className="chip">
              {ov.summary.games} games · {fmtWinRate(ov.summary.win_rate)} WR
            </span>
            {p.demo && <span className="chip demo">Demo data</span>}
          </div>
        </div>
        <div className="head-actions">
          <span className="muted" style={{ fontSize: 12 }}>
            {syncMsg ?? (r.synced_at ? `Updated ${timeAgo(r.synced_at)}` : '')}
          </span>
          <button className="btn" onClick={() => void refresh()} disabled={syncing}>
            {syncing ? 'Updating…' : 'Update'}
          </button>
        </div>
      </header>

      {p.demo && (
        <div className="banner">
          <b>Demo profile.</b> Synthetic season in the exact {g.name} API format —{' '}
          {g.configured ? (
            'search any player above to see real data.'
          ) : (
            <>
              add an API key in <code>backend/.env</code> to look up real players.
            </>
          )}
        </div>
      )}

      <div className="tabs" role="tablist">
        {(
          [
            ['overview', 'Overview'],
            ['matches', 'Match history'],
            ['pool', plural(g.character_label)],
            ['insights', 'Insights'],
          ] as [Tab, string][]
        ).map(([t, label]) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'overview' && <OverviewTab r={r} onPick={(c) => setTab('matches', { character: c })} />}
      {tab === 'matches' && <MatchesTab game={g} playerKey={key} character={characterFilter} modes={ov.modes.map((m) => m.name)} onClear={() => setTab('matches')} />}
      {tab === 'pool' && (
        <div className="grid cols-2">
          <section className="card">
            <h2>{g.character_label} pool</h2>
            <p className="sub">Click a row to see those matches</p>
            <CharacterTable game={g} rows={ov.characters} onPick={(c) => setTab('matches', { character: c })} />
          </section>
          <div className="stack">
            {ov.roles.length > 0 && (
              <section className="card">
                <h2>Roles</h2>
                <SimpleGroupTable label="Role" rows={ov.roles} />
              </section>
            )}
            {ov.maps.length > 0 && (
              <section className="card">
                <h2>Maps</h2>
                <SimpleGroupTable label="Map" rows={ov.maps} />
              </section>
            )}
            <section className="card">
              <h2>Modes</h2>
              <SimpleGroupTable label="Mode" rows={ov.modes} />
            </section>
          </div>
        </div>
      )}
      {tab === 'insights' && <InsightsTab r={r} />}
    </div>
    </MatchClipsContext.Provider>
  )
}

function OverviewTab({ r, onPick }: { r: OverviewResponse; onPick: (c: string) => void }) {
  const { game: g, overview: ov } = r
  return (
    <div className="grid cols-3">
      <div className="stack">
        <section className="card">
          <h2>Rank</h2>
          <RankCard ranks={r.profile.ranks} />
        </section>
        <section className="card">
          <h2>Top {plural(g.character_label).toLowerCase()}</h2>
          <CharacterTable game={{ ...g, card_metrics: g.card_metrics.slice(0, 1) }} rows={ov.characters} limit={6} onPick={onPick} />
        </section>
        {ov.teammates.length > 0 && (
          <section className="card">
            <h2>Played with</h2>
            <SimpleGroupTable label="Teammate" rows={ov.teammates.slice(0, 5)} />
          </section>
        )}
      </div>
      <div className="stack">
        <section className="card">
          <h2>Performance</h2>
          <StatTiles game={g} ov={ov} />
        </section>
        <section className="card">
          <h2>Insights</h2>
          <Insights items={ov.insights.slice(0, 4)} />
        </section>
        <section className="card">
          <h2>Form</h2>
          <p className="sub">Rolling {ov.trend.window}-game win rate</p>
          <FormChart ov={ov} />
        </section>
        <section>
          <div className="section-title">Recent matches</div>
          <div className="matches">
            {r.recent.map((m) => (
              <MatchCard key={m.id} game={g} match={m} playerKey={r.profile.key} />
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}

function InsightsTab({ r }: { r: OverviewResponse }) {
  const { game: g, overview: ov } = r
  return (
    <div className="grid cols-2">
      <section className="card">
        <h2>Takeaways</h2>
        <Insights items={ov.insights} />
      </section>
      <section className="card">
        <h2>What wins you games</h2>
        <p className="sub">Win-rate lift from each habit — your games split at your own median</p>
        <FactorsChart game={g} ov={ov} />
      </section>
      <section className="card">
        <h2>Trends</h2>
        <TrendChart game={g} ov={ov} />
      </section>
      <section className="card">
        <h2>Session fatigue</h2>
        <p className="sub">
          Win rate by game number in a play session · {ov.sessions.count} sessions, {ov.sessions.avg_games} games each on average
        </p>
        <TiltChart ov={ov} />
      </section>
      {ov.rank_history.length > 1 && (
        <section className="card">
          <h2>Rank history</h2>
          <RankChart ov={ov} />
        </section>
      )}
      <section className="card">
        <h2>Streaks</h2>
        <div className="tiles">
          <div className="tile">
            <div className="k">Current</div>
            <div className="v">{ov.streaks.current.length ? `${ov.streaks.current.length}${ov.streaks.current.type === 'win' ? 'W' : 'L'}` : '—'}</div>
          </div>
          <div className="tile">
            <div className="k">Best win streak</div>
            <div className="v">{ov.streaks.longest_win}</div>
          </div>
          <div className="tile">
            <div className="k">Worst loss streak</div>
            <div className="v">{ov.streaks.longest_loss}</div>
          </div>
          <div className="tile">
            <div className="k">Hours played</div>
            <div className="v">{ov.summary.hours_played ?? '—'}</div>
          </div>
        </div>
      </section>
    </div>
  )
}

const PAGE = 20

function MatchesTab({ game, playerKey, character, modes, onClear }: { game: Game; playerKey: string; character?: string; modes: string[]; onClear: () => void }) {
  const [mode, setMode] = useState('')
  const [pages, setPages] = useState<MatchPage[]>([])
  const first = useAsync(() => api.matches(game.id, playerKey, { limit: PAGE, character, mode }), [game.id, playerKey, character, mode])
  const [loadingMore, setLoadingMore] = useState(false)

  useEffect(() => {
    if (first.data) setPages([first.data])
  }, [first.data])

  const items = pages.flatMap((p) => p.items)
  const total = pages[0]?.total ?? 0
  async function more() {
    setLoadingMore(true)
    try {
      const next = await api.matches(game.id, playerKey, { limit: PAGE, offset: items.length, character, mode })
      setPages((ps) => [...ps, next])
    } finally {
      setLoadingMore(false)
    }
  }

  return (
    <div className="stack">
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <div className="seg" role="group" aria-label="Mode">
          {['', ...modes].map((m) => (
            <button key={m || 'all'} aria-pressed={mode === m} onClick={() => setMode(m)}>
              {m || 'All modes'}
            </button>
          ))}
        </div>
        {character && (
          <button className="btn small" onClick={onClear}>
            <Portrait src={null} name={character} size="xs" /> {character} ✕
          </button>
        )}
        <span className="muted" style={{ fontSize: 12, marginLeft: 'auto' }}>
          {total} matches
        </span>
      </div>
      {first.loading && !items.length ? (
        <div className="skeleton" style={{ height: 300 }} />
      ) : items.length === 0 ? (
        <div className="card empty">No matches for this filter.</div>
      ) : (
        <div className="matches">
          {items.map((m) => (
            <MatchCard key={m.id} game={game} match={m} playerKey={playerKey} />
          ))}
        </div>
      )}
      {items.length < total && (
        <button className="btn" style={{ justifySelf: 'center' }} onClick={() => void more()} disabled={loadingMore}>
          {loadingMore ? 'Loading…' : `Show more (${total - items.length} left)`}
        </button>
      )}
    </div>
  )
}

function ProfileSkeleton() {
  return (
    <div className="stack" aria-busy="true">
      <div className="skeleton" style={{ height: 90 }} />
      <div className="grid cols-3">
        <div className="skeleton" style={{ height: 320 }} />
        <div className="skeleton" style={{ height: 320 }} />
      </div>
    </div>
  )
}
