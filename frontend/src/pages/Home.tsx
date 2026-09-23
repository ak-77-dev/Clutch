import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import { Portrait } from '../components/Portrait'
import { SearchBar } from '../components/SearchBar'
import { useAsync, useGames } from '../hooks'

const BLURB: Record<string, string> = {
  lol: 'Match history, champion pool, CS/vision trends and what actually wins you games — from the official Riot API.',
  valorant: 'Agents, maps, ACS/ADR/HS% trends and rank history — via the HenrikDev API.',
  rocketleague: 'Replays from ballchasing.com: boost, positioning, rank climb and duo chemistry.',
  dota2: 'Heroes, GPM/XPM, last hits and medal history from OpenDota. No API key needed.',
  deadlock: 'Heroes, souls, accuracy and rank badges from deadlock-api.com. No API key needed.',
}

export function Home() {
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
  return (
    <div>
      <section className="hero">
        <h1>
          Every game. <span>One stats hub.</span>
        </h1>
        <p>Look up any player to get match history, trends, rank progression and plain-English insights on what separates their wins from their losses.</p>
      </section>

      <div className="grid" style={{ gap: 20 }}>
        <SearchBar game={focus} />

        <div className="game-cards">
          {shown.map((g) => (
            <div className="game-card" key={g.id} style={{ '--game': g.accent } as React.CSSProperties}>
              <h3>{g.name}</h3>
              <p className="muted" style={{ margin: 0, position: 'relative' }}>
                {BLURB[g.id]}
              </p>
              <div className={`status ${g.configured ? 'pill-ok' : 'pill-off'}`} style={{ position: 'relative' }}>
                {g.configured ? '● Live data connected' : '○ No API key — demo profile available'}
              </div>
              <div className="row">
                <button className="btn primary" style={{ background: g.accent }} onClick={() => void openDemo(g.id)} disabled={opening === g.id}>
                  {opening === g.id ? 'Loading…' : 'Try the demo profile'}
                </button>
                {!focus && (
                  <Link className="btn" to={`/${g.id}`}>
                    Search players
                  </Link>
                )}
              </div>
            </div>
          ))}
        </div>

        {recent.data && recent.data.length > 0 && (
          <section>
            <div className="section-title">Recently viewed</div>
            <div className="recent-list">
              {recent.data.map((p) => (
                <Link key={`${p.game}:${p.key}`} to={`/${p.game}/p/${encodeURIComponent(p.key)}`}>
                  <Portrait src={p.icon} name={p.name} size="xs" />
                  <span>
                    {p.name}
                    {p.tag ? <span className="muted">#{p.tag}</span> : null}
                  </span>
                  <span className="muted" style={{ fontSize: 11 }}>
                    {games.find((g) => g.id === p.game)?.name}
                  </span>
                </Link>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  )
}
