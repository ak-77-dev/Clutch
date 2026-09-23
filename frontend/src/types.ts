// Mirrors the FastAPI response models (clutch/models.py, clutch/analytics.py).

export type Fmt = 'int' | 'float1' | 'float2' | 'pct' | 'time'
export type Result = 'win' | 'loss' | 'draw' | 'remake'

export interface Metric {
  key: string
  label: string
  fmt: Fmt
  higher_is_better: boolean
  short: string | null
}

export interface Game {
  id: string
  name: string
  character_label: string
  search_hint: string
  accent: string
  metrics: Record<string, Metric>
  kpis: string[]
  trend_metrics: string[]
  factor_metrics: string[]
  card_metrics: string[]
  configured: boolean
}

export interface RankEntry {
  queue: string
  label: string | null
  tier?: string
  lp?: number | null
  value?: number | null
  wins?: number | null
  losses?: number | null
  icon?: string | null
}

export interface Profile {
  game: string
  key: string
  name: string
  tag: string | null
  icon: string | null
  level: number | null
  region: string | null
  ranks: RankEntry[]
  demo: boolean
}

export interface ScoreRow {
  name: string
  team: string
  character: string
  character_icon: string | null
  stats: Record<string, string | number | null>
  is_self: boolean
  rank: string | null
  extra: { items?: string[]; won?: boolean; mvp?: boolean }
}

export interface MatchSummary {
  game: string
  id: string
  date: string
  mode: string
  duration_s: number
  result: Result
  character: string
  character_icon: string | null
  metrics: Record<string, number>
  role: string | null
  map: string | null
  rank_label: string | null
  rank_value: number | null
  score_line: string | null
  teammates: string[]
  items: string[]
  link: string | null
}

export interface MatchDetail extends MatchSummary {
  scoreboard: ScoreRow[]
}

export interface GroupRow {
  name: string
  icon: string | null
  games: number
  wins: number
  win_rate: number | null
  avg: Record<string, number | null>
  last_played: string
}

export interface Factor {
  metric: string
  label: string
  fmt: Fmt
  median: number
  high_win_rate: number
  low_win_rate: number
  difference: number
  high_games: number
  low_games: number
}

export interface Insight {
  tone: 'positive' | 'negative' | 'tip' | 'neutral'
  text: string
}

export interface CompareStat {
  recent: number | null
  previous: number | null
  delta: number | null
}

export interface Overview {
  summary: {
    games: number
    wins: number
    losses: number
    draws: number
    win_rate: number | null
    avg: Record<string, number | null>
    hours_played: number | null
  }
  compare: { window: number; stats: Record<string, CompareStat> } | null
  streaks: { current: { type: 'win' | 'loss' | null; length: number }; longest_win: number; longest_loss: number }
  characters: GroupRow[]
  modes: GroupRow[]
  roles: GroupRow[]
  maps: GroupRow[]
  teammates: { key: string; name: string; games: number; wins: number; win_rate: number | null }[]
  sessions: {
    count: number
    avg_games: number | null
    tilt_curve: { game: string; games: number; win_rate: number | null }[]
    recent: { start: string; games: number; wins: number; win_rate: number | null }[]
  }
  win_factors: Factor[]
  rank_history: { date: string; value: number; label: string | null }[]
  insights: Insight[]
  trend: { window: number; dates: string[]; win_rate: (number | null)[] } & Record<string, (number | null)[] | number | string[]>
}

export interface OverviewResponse {
  game: Game
  profile: Profile
  synced_at: string | null
  overview: Overview
  recent: MatchSummary[]
}

export interface MatchPage {
  total: number
  offset: number
  items: MatchSummary[]
}
