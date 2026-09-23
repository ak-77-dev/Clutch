import { lazy, Suspense } from 'react'
import { BrowserRouter, Link, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { api } from './api'
import { SearchBar } from './components/SearchBar'
import { DesktopProvider, Rail, Titlebar } from './components/Shell'
import { GamesContext, useAsync, useGame } from './hooks'
import { Home, StatsHub } from './pages/Home'

// Pages that pull in the charting library or heavier UI load on demand.
const ProfilePage = lazy(() => import('./pages/ProfilePage').then((m) => ({ default: m.ProfilePage })))
const Library = lazy(() => import('./pages/Library').then((m) => ({ default: m.Library })))
const GameDetail = lazy(() => import('./pages/GameDetail').then((m) => ({ default: m.GameDetail })))
const Clips = lazy(() => import('./pages/Clips').then((m) => ({ default: m.Clips })))
const Playtime = lazy(() => import('./pages/Playtime').then((m) => ({ default: m.Playtime })))
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((m) => ({ default: m.SettingsPage })))

function TopBar() {
  const { game } = useParams()
  const meta = useGame(game)
  return (
    <div className="topbar">
      <Link to="/stats" className="crumb">
        Stats
      </Link>
      {meta && <span className="crumb">/ {meta.name}</span>}
      <SearchBar game={game} />
    </div>
  )
}

function Page({ children, search = false }: { children: React.ReactNode; search?: boolean }) {
  const { pathname } = useLocation()
  return (
    <main className="main">
      {search && <TopBar />}
      <div className="content" key={pathname}>
        <Suspense fallback={<div className="skeleton" style={{ height: 360 }} />}>{children}</Suspense>
      </div>
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
  if (!games.data) return <div className="content muted mono">Loading…</div>
  return (
    <GamesContext.Provider value={games.data}>
      <BrowserRouter>
        <DesktopProvider>
          <Titlebar />
          <div className="shell">
            <Rail />
            <Routes>
              <Route path="/" element={<Page><Home /></Page>} />
              <Route path="/library" element={<Page><Library /></Page>} />
              <Route path="/library/:id" element={<Page><GameDetail /></Page>} />
              <Route path="/clips" element={<Page><Clips /></Page>} />
              <Route path="/playtime" element={<Page><Playtime /></Page>} />
              <Route path="/settings" element={<Page><SettingsPage /></Page>} />
              <Route path="/stats" element={<Page><StatsHub /></Page>} />
              <Route path="/:game" element={<Page><StatsHub /></Page>} />
              <Route path="/:game/p/:key" element={<Page search><ProfilePage /></Page>} />
              <Route path="*" element={<Page><div className="card empty">Page not found. <Link to="/">Go home</Link></div></Page>} />
            </Routes>
          </div>
        </DesktopProvider>
      </BrowserRouter>
    </GamesContext.Provider>
  )
}
