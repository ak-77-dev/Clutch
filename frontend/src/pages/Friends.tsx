import { useState } from 'react'
import { Link } from 'react-router-dom'
import { PlusIcon, RefreshIcon, TrashIcon, UsersIcon } from '../components/Icons'
import { Portrait } from '../components/Portrait'
import { useDesktop } from '../components/Shell'
import { desktop } from '../desktop'
import { fmtDuration, timeAgo } from '../format'
import { useAsync, useGames } from '../hooks'
import { DesktopOnly } from './DesktopOnly'

export function Friends() {
  return (
    <DesktopOnly feature="The friends feed">
      <FriendsInner />
    </DesktopOnly>
  )
}

function FriendsInner() {
  const games = useGames()
  const { toast } = useDesktop()
  const feed = useAsync(() => desktop.friends(), [])
  const [game, setGame] = useState(games.find((g) => g.configured)?.id ?? games[0]?.id ?? '')
  const [query, setQuery] = useState('')
  const [adding, setAdding] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const meta = games.find((g) => g.id === game)

  async function add(e: React.FormEvent) {
    e.preventDefault()
    setAdding(true)
    try {
      const f = await desktop.addFriend(game, query.trim())
      toast({ title: `Following ${f.name}`, sub: meta?.name })
      setQuery('')
      feed.reload()
    } catch (err) {
      toast({ title: 'Couldn’t find that player', sub: (err as Error).message, tone: 'error' })
    } finally {
      setAdding(false)
    }
  }

  async function refresh() {
    setRefreshing(true)
    try {
      await desktop.refreshFriends()
      feed.reload()
    } finally {
      setRefreshing(false)
    }
  }

  const cards = feed.data?.cards ?? []
  const items = feed.data?.items ?? []
  return (
    <div>
      <div className="page-head" data-echo="squad">
        <div>
          <div className="kicker bare">
            <b>06</b> Friends
          </div>
          <h1>
            The <em>squad</em>
          </h1>
        </div>
        <div className="aside">
          {cards.length > 0 && (
            <button className="btn" onClick={() => void refresh()} disabled={refreshing}>
              <RefreshIcon /> {refreshing ? 'Syncing…' : 'Sync now'}
            </button>
          )}
        </div>
      </div>

      <form className="card friend-add" onSubmit={(e) => void add(e)}>
        <div className="seg" role="group" aria-label="Game">
          {games.map((g) => (
            <button type="button" key={g.id} aria-pressed={game === g.id} onClick={() => setGame(g.id)}>
              {g.name}
            </button>
          ))}
        </div>
        <label className="field" style={{ flex: 1, minWidth: 220 }}>
          <UsersIcon />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={meta?.search_hint ?? 'Player'} aria-label="Friend's player name" />
        </label>
        <button className="btn primary" disabled={!query.trim() || adding}>
          <PlusIcon /> {adding ? 'Looking up…' : 'Follow'}
        </button>
        <p className="hint" style={{ flexBasis: '100%', margin: 0 }}>
          Friends are public stats profiles, not Clutch accounts. Clutch has no server, so nobody is notified and nothing about you is shared. Profiles sync every 15 minutes.
        </p>
      </form>

      {feed.loading && !feed.data ? (
        <div className="skeleton" style={{ height: 260 }} />
      ) : cards.length === 0 ? (
        <div className="card empty">
          <UsersIcon className="empty-icon" />
          <span className="display">Nobody here yet</span>
          Follow a friend’s Riot ID, Steam account or Epic name to see their ranks and recent games next to yours.
        </div>
      ) : (
        <div className="grid cols-1-2">
          <div className="stack">
            {cards.map((f) => {
              const g = games.find((x) => x.id === f.game)
              return (
                <div key={`${f.game}:${f.key}`} className="friend" style={{ '--game': g?.accent } as React.CSSProperties}>
                  <Portrait src={f.icon} name={f.name} size="sm" />
                  <Link to={`/${f.game}/p/${encodeURIComponent(f.key)}`} style={{ minWidth: 0, flex: 1 }}>
                    <div className="t">
                      {f.name}
                      {f.tag ? <span className="muted">#{f.tag}</span> : null}
                    </div>
                    <div className="s">
                      {g?.name}
                      {f.rank ? ` · ${f.rank}` : ''}
                      {f.last_played ? ` · played ${timeAgo(f.last_played)}` : ''}
                      {f.error ? ' · couldn’t load' : ''}
                    </div>
                  </Link>
                  {f.wins !== undefined && f.wins + (f.losses ?? 0) > 0 && (
                    <span className="mono form" title="Last 10 games">
                      <span className="w">{f.wins}W</span> <span className="l">{f.losses}L</span>
                    </span>
                  )}
                  <button className="icon-btn" title="Unfollow" onClick={() => void desktop.removeFriend(f.game, f.key).then(feed.reload, () => {})}>
                    <TrashIcon />
                  </button>
                </div>
              )
            })}
          </div>

          <section className="card">
            <h2>Latest games</h2>
            {items.length === 0 ? (
              <p className="muted" style={{ margin: 0 }}>
                No recent matches yet.
              </p>
            ) : (
              <div className="feed">
                {items.map((m) => (
                  <Link key={`${m.friend_key}:${m.id}`} to={`/${m.game}/p/${encodeURIComponent(m.friend_key)}`} className={`feed-item ${m.result}`}>
                    <Portrait src={m.character_icon} name={m.character} size="xs" />
                    <div style={{ minWidth: 0 }}>
                      <div className="t">
                        <b>{m.friend}</b> {m.result === 'win' ? 'won' : m.result === 'loss' ? 'lost' : 'played'} as {m.character}
                        {m.score_line ? ` · ${m.score_line}` : ''}
                      </div>
                      <div className="s">
                        {games.find((g) => g.id === m.game)?.name} · {m.mode}
                        {m.map ? ` · ${m.map}` : ''} · {fmtDuration(m.duration_s)}
                      </div>
                    </div>
                    <span className="when mono">{timeAgo(m.date)}</span>
                  </Link>
                ))}
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  )
}
