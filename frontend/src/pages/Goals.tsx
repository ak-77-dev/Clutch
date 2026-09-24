import { useEffect, useState } from 'react'
import { api } from '../api'
import { TargetIcon, TrashIcon } from '../components/Icons'
import { useDesktop } from '../components/Shell'
import { desktop, useDesktopEvents, type Goal, type GoalKind } from '../desktop'
import { useAsync, useGames } from '../hooks'
import { DesktopOnly } from './DesktopOnly'

const KINDS: { id: GoalKind; label: string; blurb: string; unit: string; placeholder: number; stats?: boolean }[] = [
  { id: 'daily_cap', label: 'Daily cap', blurb: 'Play at most this many hours a day. The in-game panel warns you at 80%.', unit: 'hours / day', placeholder: 3 },
  { id: 'weekly_hours', label: 'Practice hours', blurb: 'Put in at least this many hours this week.', unit: 'hours / week', placeholder: 10 },
  { id: 'clips', label: 'Clips', blurb: 'Save this many clips this week.', unit: 'clips / week', placeholder: 15 },
  { id: 'win_rate', label: 'Win rate', blurb: 'Win this share of your last 20 games (counts once you have 10).', unit: '%', placeholder: 55, stats: true },
  { id: 'rank', label: 'Rank', blurb: 'Reach a ladder position on a linked account.', unit: 'rank value', placeholder: 0, stats: true },
]

export function Goals() {
  return (
    <DesktopOnly feature="Goals">
      <GoalsInner />
    </DesktopOnly>
  )
}

export function goalTitle(g: Goal, gameName?: string): string {
  const on = gameName ? ` · ${gameName}` : ''
  switch (g.kind) {
    case 'daily_cap':
      return `At most ${g.target}h a day${on}`
    case 'weekly_hours':
      return `${g.target}h this week${on}`
    case 'clips':
      return `${g.target} clips this week${on}`
    case 'win_rate':
      return `${g.target}% win rate${on}`
    case 'rank':
      return `Reach ${g.label || `rank ${g.target}`}${on}`
  }
}

const STATE_TEXT: Record<Goal['progress']['state'], string> = {
  ok: 'on it',
  close: 'close to the cap',
  over: 'over the cap',
  done: 'done',
  unlinked: 'link the account',
  unranked: 'unranked',
}

/** "2.5h today", "54% over 20 games", "3 clips this week", "Gold 2". */
function progressText(g: Goal): string {
  const p = g.progress
  if (p.value === null) return '—'
  if (g.kind === 'rank') return p.current ?? String(Math.round(p.value))
  if (g.kind === 'clips') return `${p.value} ${p.unit}`
  return `${p.value.toFixed(p.value < 10 ? 1 : 0)}${p.unit}` // units start with "h " or "% "
}

export function GoalRow({ goal, gameName, onDelete }: { goal: Goal; gameName?: string; onDelete?: () => void }) {
  const p = goal.progress
  return (
    <div className={`goal ${p.state}`}>
      <div className="goal-top">
        <div style={{ minWidth: 0 }}>
          <div className="t">{goalTitle(goal, gameName)}</div>
          <div className="s">
            {progressText(goal)} · {STATE_TEXT[p.state]}
          </div>
        </div>
        <div className="pct">{Math.round(p.progress * 100)}%</div>
        {onDelete && (
          <button className="icon-btn" title="Remove goal" onClick={onDelete}>
            <TrashIcon />
          </button>
        )}
      </div>
      <div className="goal-bar">
        <i style={{ width: `${Math.min(100, p.progress * 100)}%` }} />
        {goal.kind === 'daily_cap' && <span className="mark" style={{ left: '80%' }} />}
      </div>
    </div>
  )
}

function GoalsInner() {
  const games = useGames()
  const { settings, toast } = useDesktop()
  const goals = useAsync(() => desktop.goals(), [])
  const library = useAsync(() => desktop.library(), [])
  const [kind, setKind] = useState<GoalKind>('daily_cap')
  const [target, setTarget] = useState('')
  const [game, setGame] = useState('')
  const [label, setLabel] = useState('')
  const [current, setCurrent] = useState<{ label: string | null; value: number | null } | null>(null)
  const spec = KINDS.find((k) => k.id === kind)!
  const linked = Object.keys(settings?.linked_profiles ?? {})
  const statGames = games.filter((g) => linked.includes(g.id))

  useDesktopEvents((e) => {
    if (e.type === 'goal' || e.type === 'session_end' || e.type === 'clip_saved') goals.reload()
  })

  function pickKind(k: GoalKind) {
    setKind(k)
    setGame(KINDS.find((x) => x.id === k)?.stats ? (statGames[0]?.id ?? '') : '')
    setTarget('')
    setLabel('')
  }

  // For rank goals, show where the player is now so the target makes sense.
  useEffect(() => {
    setCurrent(null)
    const key = settings?.linked_profiles[game]
    if (kind !== 'rank' || !game || !key) return
    let live = true
    api.overview(game, key).then(
      (o) => {
        const r = o.profile.ranks[0]
        if (live) setCurrent(r ? { label: r.label, value: r.value ?? null } : { label: null, value: null })
      },
      () => {},
    )
    return () => {
      live = false
    }
  }, [kind, game, settings])

  async function add(e: React.FormEvent) {
    e.preventDefault()
    try {
      await desktop.addGoal({ kind, target: Number(target), game_id: game || null, label: label.trim() || null })
      setTarget('')
      setLabel('')
      goals.reload()
    } catch (err) {
      toast({ title: 'Couldn’t add that goal', sub: (err as Error).message, tone: 'error' })
    }
  }

  const nameOf = (id: string | null) => (id ? (games.find((g) => g.id === id)?.name ?? library.data?.find((g) => g.id === id)?.name ?? id) : undefined)
  const list = goals.data ?? []
  const done = list.filter((g) => g.progress.state === 'done').length
  return (
    <div>
      <div className="page-head" data-echo="goals">
        <div>
          <div className="kicker bare">
            <b>05</b> Goals
          </div>
          <h1>
            Set the <em>bar</em>
          </h1>
        </div>
        <div className="aside">
          {list.length > 0 && (
            <span className="mono muted" style={{ fontSize: 12 }}>
              {done}/{list.length} done · checked every minute
            </span>
          )}
        </div>
      </div>

      <div className="grid cols-2-1">
        <section className="card">
          <h2>Your goals</h2>
          {goals.loading && !goals.data ? (
            <div className="skeleton" style={{ height: 160 }} />
          ) : list.length === 0 ? (
            <div className="empty" style={{ padding: 28 }}>
              <TargetIcon className="empty-icon" />
              <span className="display" style={{ fontSize: 30 }}>
                No goals yet
              </span>
              Cap your hours, set a practice target, or chase a rank. Clutch tracks them from your playtime, clips and linked stats.
            </div>
          ) : (
            <div className="goals">
              {list.map((g) => (
                <GoalRow key={g.id} goal={g} gameName={nameOf(g.game_id)} onDelete={() => void desktop.deleteGoal(g.id).then(goals.reload, () => {})} />
              ))}
            </div>
          )}
        </section>

        <form className="card goal-form" onSubmit={(e) => void add(e)}>
          <h2>New goal</h2>
          <div className="kind-pick" role="group" aria-label="Goal type">
            {KINDS.map((k) => (
              <button
                type="button"
                key={k.id}
                aria-pressed={kind === k.id}
                disabled={k.stats && statGames.length === 0}
                title={k.stats && statGames.length === 0 ? 'Link a stats account first' : undefined}
                onClick={() => pickKind(k.id)}
              >
                {k.label}
              </button>
            ))}
          </div>
          <p className="hint">{spec.blurb}</p>

          <label className="flabel">
            Game
            <span className="field">
              <select value={game} onChange={(e) => setGame(e.target.value)}>
                {spec.stats ? (
                  statGames.map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.name}
                    </option>
                  ))
                ) : (
                  <>
                    <option value="">All games</option>
                    {(library.data ?? []).map((g) => (
                      <option key={g.id} value={g.id}>
                        {g.name}
                      </option>
                    ))}
                  </>
                )}
              </select>
            </span>
          </label>

          {kind === 'rank' && current && (
            <p className="hint" style={{ color: 'var(--text-2)' }}>
              You’re at <b>{current.label ?? 'unranked'}</b>
              {current.value !== null ? ` (rank value ${Math.round(current.value)})` : ''}. Rank values go up as you climb, so pick a higher number.
            </p>
          )}

          <label className="flabel">
            Target
            <span className="field">
              <input
                type="number"
                min={0}
                step="any"
                required
                value={target}
                placeholder={String(kind === 'rank' && current?.value ? Math.round(current.value + 100) : spec.placeholder)}
                onChange={(e) => setTarget(e.target.value)}
              />
              <span className="mono muted" style={{ fontSize: 11 }}>
                {spec.unit}
              </span>
            </span>
          </label>

          {kind === 'rank' && (
            <label className="flabel">
              Name it
              <span className="field">
                <input value={label} maxLength={40} placeholder="e.g. Diamond 1" onChange={(e) => setLabel(e.target.value)} />
              </span>
            </label>
          )}

          <button className="btn primary" type="submit" disabled={!target || (spec.stats && !game)}>
            <TargetIcon /> Add goal
          </button>
        </form>
      </div>
    </div>
  )
}
