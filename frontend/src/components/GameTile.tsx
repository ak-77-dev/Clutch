import { useState } from 'react'
import { Link } from 'react-router-dom'
import { desktop, type LibraryGame } from '../desktop'
import { fmtHours } from '../format'
import { PlayIcon } from './Icons'

export const SOURCE_LABEL: Record<string, string> = {
  steam: 'Steam',
  epic: 'Epic',
  riot: 'Riot',
  battlenet: 'Battle.net',
  ubisoft: 'Ubisoft',
  ea: 'EA',
  rockstar: 'Rockstar',
  xbox: 'Xbox',
  custom: 'Added',
}

/** Portrait cover from Steam's CDN, falling back to a designed tile (real icon + game colour). */
export function Cover({ game }: { game: LibraryGame }) {
  const candidates = [game.art.cover, game.art.header].filter(Boolean) as string[]
  const [idx, setIdx] = useState(0)
  const [iconFailed, setIconFailed] = useState(false)
  const src = candidates[idx]
  return (
    <div className="cover" style={{ '--tint': game.art.accent ?? '#3a3a40' } as React.CSSProperties}>
      {src ? (
        <img className="art" src={src} alt="" loading="lazy" onError={() => setIdx((i) => i + 1)} />
      ) : (
        <div className="fallback">
          {!iconFailed ? <img src={desktop.iconUrl(game.id)} alt="" onError={() => setIconFailed(true)} /> : <span />}
          <span className="fname">{game.name}</span>
        </div>
      )}
    </div>
  )
}

export function GameTile({ game, onLaunch }: { game: LibraryGame; onLaunch: (g: LibraryGame) => void }) {
  return (
    <div className="gtile brackets">
      <Link to={`/library/${encodeURIComponent(game.id)}`} aria-label={game.name}>
        <Cover game={game} />
      </Link>
      <div className="badges">
        {game.running && <span className="badge live">Live</span>}
        {game.stats_game && <span className="badge stats">Stats</span>}
      </div>
      <button className="play" onClick={() => onLaunch(game)} aria-label={`Play ${game.name}`} title={`Play ${game.name}`}>
        <PlayIcon />
      </button>
      <Link to={`/library/${encodeURIComponent(game.id)}`}>
        <div className="gname" title={game.name}>
          {game.name}
        </div>
        <div className="gmeta">
          <span className="src">{SOURCE_LABEL[game.source] ?? game.source}</span>
          {game.playtime > 0 && <span>{fmtHours(game.playtime)}</span>}
          {game.clips > 0 && <span>{game.clips} clips</span>}
        </div>
      </Link>
    </div>
  )
}
