import type { Game, MatchDetail, MatchPage, OverviewResponse, Profile } from './types'

export class ApiError extends Error {
  constructor(
    message: string,
    public code: string,
    public status: number,
  ) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`/api${path}`, init)
  } catch {
    throw new ApiError('Can’t reach the Clutch server. Is `clutch serve` running?', 'NETWORK', 0)
  }
  const body = await res.json().catch(() => null)
  if (!res.ok) {
    const err = body?.detail ?? body
    throw new ApiError(err?.message ?? `Request failed (${res.status})`, err?.error ?? 'ERROR', res.status)
  }
  return body as T
}

const enc = encodeURIComponent

export const api = {
  games: () => request<Game[]>('/games'),
  recent: () => request<Profile[]>('/recent'),
  search: (game: string, q: string) => request<Profile>(`/${game}/search?q=${enc(q)}`),
  overview: (game: string, key: string) => request<OverviewResponse>(`/${game}/players/${enc(key)}/overview`),
  matches: (game: string, key: string, params: { offset?: number; limit?: number; character?: string; mode?: string }) => {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '') qs.set(k, String(v))
    return request<MatchPage>(`/${game}/players/${enc(key)}/matches?${qs}`)
  },
  match: (game: string, key: string, id: string) => request<MatchDetail>(`/${game}/players/${enc(key)}/matches/${enc(id)}`),
  sync: (game: string, key: string) =>
    request<{ new: number; failed: number; synced_at: string | null; demo: boolean }>(`/${game}/players/${enc(key)}/sync`, { method: 'POST' }),
}
