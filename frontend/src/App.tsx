import { lazy, Suspense } from 'react'
import { BrowserRouter, Link, Route, Routes, useParams } from 'react-router-dom'
import { api } from './api'
import { SearchBar } from './components/SearchBar'
import { Sidebar } from './components/Sidebar'
import { GamesContext, useAsync, useGame } from './hooks'
import { Home } from './pages/Home'

// Profile pages pull in the charting library; load them on demand.
const ProfilePage = lazy(() => import('./pages/ProfilePage').then((m) => ({ default: m.ProfilePage })))

function TopBar({ search }: { search: boolean }) {
  const { game } = useParams()
  const meta = useGame(game)
  return (
    <div className="topbar">
      <Link to="/" className="crumb">
        Clutch
      </Link>
      {meta && <span className="crumb muted">/ {meta.name}</span>}
      {search && <SearchBar game={game} />}
    </div>
  )
}

function Page({ children, search = true }: { children: React.ReactNode; search?: boolean }) {
  return (
    <main className="main">
      <TopBar search={search} />
      <div className="content">{children}</div>
    </main>
  )
}

export default function App() {
  const games = useAsync(() => api.games(), [])
  if (games.error) {
    return (
      <div className="content">
        <div className="card empty error">{games.error.message}</div>
      </div>
    )
  }
  if (!games.data) return <div className="content muted">Loading…</div>
  return (
    <GamesContext.Provider value={games.data}>
      <BrowserRouter>
        <div className="shell">
          <Sidebar />
          <Routes>
            {/* Home pages have their own hero search, so the top bar omits it. */}
            <Route path="/" element={<Page search={false}><Home /></Page>} />
            <Route path="/:game" element={<Page search={false}><Home /></Page>} />
            <Route
              path="/:game/p/:key"
              element={
                <Page>
                  <Suspense fallback={<div className="skeleton" style={{ height: 320 }} />}>
                    <ProfilePage />
                  </Suspense>
                </Page>
              }
            />
            <Route path="*" element={<Page><div className="card empty">Page not found. <Link to="/">Go home</Link></div></Page>} />
          </Routes>
        </div>
      </BrowserRouter>
    </GamesContext.Provider>
  )
}
