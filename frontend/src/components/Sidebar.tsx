import { NavLink } from 'react-router-dom'
import { useGames } from '../hooks'

// Neutral monograms (not official logos) tinted with each game's accent.
const GLYPH: Record<string, string> = { lol: 'LoL', valorant: 'VAL', rocketleague: 'RL', dota2: 'D2', deadlock: 'DL' }

export function Logo({ size = 40 }: { size?: number }) {
  return (
    <svg className="logo" width={size} height={size} viewBox="0 0 32 32" aria-label="Clutch">
      <rect width="32" height="32" rx="9" fill="#12151d" />
      <path d="M21.5 10.5a8 8 0 1 0 0 11" fill="none" stroke="#7c5cff" strokeWidth="4" strokeLinecap="round" />
    </svg>
  )
}

export function Sidebar() {
  const games = useGames()
  return (
    <nav className="sidebar" aria-label="Games">
      <NavLink to="/" aria-label="Home">
        <Logo />
      </NavLink>
      {games.map((g) => (
        <NavLink
          key={g.id}
          to={`/${g.id}`}
          className={({ isActive }) => `game-btn${isActive ? ' active' : ''}`}
          style={{ '--game': g.accent } as React.CSSProperties}
          title={`${g.name}${g.configured ? '' : ' (demo only — no API key)'}`}
        >
          <span style={{ fontSize: GLYPH[g.id]?.length > 2 ? 11 : 13 }}>{GLYPH[g.id] ?? g.name.slice(0, 2)}</span>
          {g.configured && <span className="dot" aria-label="live data" />}
        </NavLink>
      ))}
      <div className="spacer" />
    </nav>
  )
}
