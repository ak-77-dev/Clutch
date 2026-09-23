import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useGames } from '../hooks'

export function SearchBar({ game: initialGame }: { game?: string }) {
  const games = useGames()
  const navigate = useNavigate()
  const [game, setGame] = useState(initialGame ?? games[0]?.id ?? 'lol')
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const meta = games.find((g) => g.id === game)

  useEffect(() => {
    if (initialGame) setGame(initialGame)
  }, [initialGame])

  async function submit(query: string) {
    if (!query.trim()) return
    setBusy(true)
    setError(null)
    try {
      const profile = await api.search(game, query.trim())
      navigate(`/${game}/p/${encodeURIComponent(profile.key)}`)
      setQ('')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ flex: 1, maxWidth: 560 }}>
      <form
        className="search"
        role="search"
        onSubmit={(e) => {
          e.preventDefault()
          void submit(q)
        }}
      >
        <select value={game} onChange={(e) => setGame(e.target.value)} aria-label="Game">
          {games.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={meta ? `${meta.search_hint} — or "demo"` : 'Search a player'}
          aria-label="Player"
        />
        <button className="btn primary small" disabled={busy || !q.trim()} type="submit">
          {busy ? 'Searching…' : 'Search'}
        </button>
      </form>
      {error && (
        <div className="error" role="alert" style={{ fontSize: 12, marginTop: 6 }}>
          {error}{' '}
          <button className="btn small" type="button" onClick={() => void submit('demo')}>
            Open demo profile
          </button>
        </div>
      )}
    </div>
  )
}
