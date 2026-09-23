import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import type { Game } from './types'

export interface AsyncState<T> {
  data: T | null
  error: Error | null
  loading: boolean
  reload: () => void
}

/** Runs `fn` whenever `deps` change; stale responses from earlier runs are dropped. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [state, setState] = useState<{ data: T | null; error: Error | null; loading: boolean }>({ data: null, error: null, loading: true })
  const [tick, setTick] = useState(0)
  const run = useRef(0)

  useEffect(() => {
    const id = ++run.current
    setState((s) => ({ ...s, loading: true, error: null }))
    fn().then(
      (data) => id === run.current && setState({ data, error: null, loading: false }),
      (error: Error) => id === run.current && setState({ data: null, error, loading: false }),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])

  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { ...state, reload }
}

export const GamesContext = createContext<Game[]>([])

export function useGames(): Game[] {
  return useContext(GamesContext)
}

export function useGame(id: string | undefined): Game | undefined {
  return useGames().find((g) => g.id === id)
}
