"""Game-agnostic performance analytics.

Operates on normalized ``Match`` objects + the game's ``GameMeta``, so the
same engine powers Rocket League, League of Legends and Valorant profiles.
All functions are pure and return JSON-serializable dicts.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from clutch.models import GameMeta, Match

SESSION_GAP = timedelta(minutes=45)


def parse_date(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def counted(matches: Sequence[Match]) -> list[Match]:
    """Chronological, remakes dropped."""
    return sorted((m for m in matches if m.counts), key=lambda m: parse_date(m.date))


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _r(v: float | None, nd: int = 2) -> float | None:
    return None if v is None else round(v, nd)


def _metric_vals(matches: Sequence[Match], key: str) -> list[float]:
    return [m.metrics[key] for m in matches if m.metrics.get(key) is not None]


def win_rate(matches: Sequence[Match]) -> float | None:
    decided = [m for m in matches if m.result in ("win", "loss", "draw")]
    return 100 * sum(m.won for m in decided) / len(decided) if decided else None


def summarize(meta: GameMeta, matches: Sequence[Match]) -> dict[str, Any]:
    wins = sum(m.won for m in matches)
    losses = sum(m.result == "loss" for m in matches)
    return {
        "games": len(matches),
        "wins": wins,
        "losses": losses,
        "draws": len(matches) - wins - losses,
        "win_rate": _r(win_rate(matches), 1),
        "avg": {mt.key: _r(_mean(_metric_vals(matches, mt.key))) for mt in meta.metrics},
        "hours_played": _r(sum(m.duration_s for m in matches) / 3600, 1),
    }


def compare_windows(meta: GameMeta, matches: Sequence[Match], window: int = 20) -> dict[str, Any] | None:
    if len(matches) < 2 * window:
        window = len(matches) // 2
    if window < 5:
        return None
    recent, previous = matches[-window:], matches[-2 * window : -window]
    stats: dict[str, Any] = {}
    for key in ("win_rate", *(m.key for m in meta.metrics)):
        if key == "win_rate":
            r, p = win_rate(recent), win_rate(previous)
        else:
            r, p = _mean(_metric_vals(recent, key)), _mean(_metric_vals(previous, key))
        stats[key] = {"recent": _r(r), "previous": _r(p), "delta": _r(r - p) if r is not None and p is not None else None}
    return {"window": window, "stats": stats}


def rolling(
    matches: Sequence[Match], fn: Callable[[Match], float | None], window: int = 10, min_periods: int = 3
) -> list[float | None]:
    """Trailing mean; ``None`` until ``min_periods`` values exist (one game isn't a trend)."""
    out: list[float | None] = []
    buf: list[float] = []
    for m in matches:
        v = fn(m)
        if v is not None:
            buf.append(v)
            if len(buf) > window:
                buf.pop(0)
        out.append(_r(_mean(buf)) if len(buf) >= min_periods else None)
    return out


def streaks(matches: Sequence[Match]) -> dict[str, Any]:
    longest = {"win": 0, "loss": 0}
    kind: str | None = None
    run = 0
    for m in matches:
        k = "win" if m.won else "loss"
        run = run + 1 if k == kind else 1
        kind = k
        longest[k] = max(longest[k], run)
    return {"current": {"type": kind, "length": run}, "longest_win": longest["win"], "longest_loss": longest["loss"]}


def _group_rows(meta: GameMeta, groups: dict[str, list[Match]], label_key: str, icons: dict[str, str | None]) -> list[dict[str, Any]]:
    rows = []
    for name, ms in groups.items():
        rows.append(
            {
                label_key: name,
                "icon": icons.get(name),
                "games": len(ms),
                "wins": sum(m.won for m in ms),
                "win_rate": _r(win_rate(ms), 1),
                "avg": {k: _r(_mean(_metric_vals(ms, k))) for k in meta.card_metrics},
                "last_played": ms[-1].date,
            }
        )
    return sorted(rows, key=lambda r: (-r["games"], -(r["win_rate"] or 0)))


def character_pool(meta: GameMeta, matches: Sequence[Match]) -> list[dict[str, Any]]:
    groups: dict[str, list[Match]] = defaultdict(list)
    icons: dict[str, str | None] = {}
    for m in matches:
        groups[m.character].append(m)
        icons[m.character] = m.character_icon
    return _group_rows(meta, groups, "name", icons)


def by_mode(meta: GameMeta, matches: Sequence[Match]) -> list[dict[str, Any]]:
    groups: dict[str, list[Match]] = defaultdict(list)
    for m in matches:
        groups[m.mode].append(m)
    return _group_rows(meta, groups, "name", {})


def by_role(meta: GameMeta, matches: Sequence[Match]) -> list[dict[str, Any]]:
    groups: dict[str, list[Match]] = defaultdict(list)
    for m in matches:
        if m.role:
            groups[m.role].append(m)
    return _group_rows(meta, groups, "name", {})


def by_map(meta: GameMeta, matches: Sequence[Match]) -> list[dict[str, Any]]:
    groups: dict[str, list[Match]] = defaultdict(list)
    for m in matches:
        if m.map:
            groups[m.map].append(m)
    return _group_rows(meta, groups, "name", {})


def teammates(matches: Sequence[Match], min_games: int = 3) -> list[dict[str, Any]]:
    groups: dict[str, list[Match]] = defaultdict(list)
    names: dict[str, str] = {}
    for m in matches:
        for key, name in zip(m.team_keys, m.teammates, strict=False):
            groups[key].append(m)
            names[key] = name
    rows = [
        {"key": k, "name": names[k], "games": len(ms), "wins": sum(m.won for m in ms), "win_rate": _r(win_rate(ms), 1)}
        for k, ms in groups.items()
        if len(ms) >= min_games
    ]
    return sorted(rows, key=lambda r: (-r["games"], -(r["win_rate"] or 0)))


def split_sessions(matches: Sequence[Match], gap: timedelta = SESSION_GAP) -> list[list[Match]]:
    sessions: list[list[Match]] = []
    last_end: datetime | None = None
    for m in matches:
        start = parse_date(m.date)
        if last_end is None or start - last_end > gap:
            sessions.append([])
        sessions[-1].append(m)
        last_end = start + timedelta(seconds=m.duration_s or 0)
    return sessions


def session_analysis(matches: Sequence[Match], max_index: int = 6) -> dict[str, Any]:
    sessions = split_sessions(matches)
    by_index: dict[int, list[Match]] = defaultdict(list)
    for s in sessions:
        for i, m in enumerate(s, 1):
            by_index[min(i, max_index)].append(m)
    curve = [
        {"game": f"{i}+" if i == max_index else str(i), "games": len(by_index[i]), "win_rate": _r(win_rate(by_index[i]), 1)}
        for i in range(1, max_index + 1)
        if by_index.get(i)
    ]
    recent = [{"start": s[0].date, "games": len(s), "wins": sum(m.won for m in s), "win_rate": _r(win_rate(s), 1)} for s in sessions[-10:]][
        ::-1
    ]
    return {"count": len(sessions), "avg_games": _r(_mean([len(s) for s in sessions]), 1), "tilt_curve": curve, "recent": recent}


def win_factors(meta: GameMeta, matches: Sequence[Match], min_side: int = 8) -> list[dict[str, Any]]:
    out = []
    for key in meta.factor_metrics:
        mt = meta.metric(key)
        pairs = [(m.metrics[key], m) for m in matches if m.metrics.get(key) is not None]
        if len(pairs) < 2 * min_side:
            continue
        median = statistics.median(v for v, _ in pairs)
        high = [m for v, m in pairs if v > median]
        low = [m for v, m in pairs if v <= median]
        if len(high) < min_side or len(low) < min_side:
            continue
        hi, lo = win_rate(high), win_rate(low)
        if hi is None or lo is None:
            continue
        out.append(
            {
                "metric": key,
                "label": mt.label,
                "fmt": mt.fmt,
                "median": _r(median),
                "high_win_rate": _r(hi, 1),
                "low_win_rate": _r(lo, 1),
                "difference": _r(hi - lo, 1),
                "high_games": len(high),
                "low_games": len(low),
            }
        )
    return sorted(out, key=lambda r: -abs(r["difference"]))


def rank_history(matches: Sequence[Match]) -> list[dict[str, Any]]:
    pts: list[dict[str, Any]] = []
    for m in matches:
        if m.rank_value is None:
            continue
        if not pts or pts[-1]["value"] != m.rank_value:
            pts.append({"date": m.date, "value": m.rank_value, "label": m.rank_label})
    return pts


def _fmt_value(fmt: str, v: float) -> str:
    if fmt == "pct":
        return f"{v:.0f}%"
    if fmt == "int":
        return f"{v:.0f}"
    if fmt == "float2":
        return f"{v:.2f}"
    if fmt == "time":
        return f"{int(v // 60)}:{int(v % 60):02d}"
    return f"{v:.1f}"


def _inline(label: str) -> str:
    """Label for mid-sentence use: lowercase words, keep acronyms ("CS / min", "KDA")."""
    first, _, rest = label.partition(" ")
    return label if first.isupper() and len(first) > 1 else f"{first.lower()} {rest}".strip()


def insights(meta: GameMeta, matches: Sequence[Match], max_items: int = 6) -> list[dict[str, str]]:
    """Plain-English takeaways, strongest first; every one is sample-size guarded."""
    if len(matches) < 10:
        return [{"tone": "neutral", "text": f"{len(matches)} games tracked — insights unlock at 10+ games."}]
    items: list[tuple[float, dict[str, str]]] = []

    for f in win_factors(meta, matches)[:3]:
        d = f["difference"]
        if abs(d) < 8:
            continue
        higher_helps = d > 0
        good = f["high_win_rate"] if higher_helps else f["low_win_rate"]
        bad = f["low_win_rate"] if higher_helps else f["high_win_rate"]
        side = ">" if higher_helps else "≤"
        items.append(
            (
                abs(d),
                {
                    "tone": "tip",
                    "text": f"You win {good:.0f}% of games with {_inline(f['label'])} {side} {_fmt_value(f['fmt'], f['median'])} "
                    f"vs {bad:.0f}% otherwise.",
                },
            )
        )

    cmp = compare_windows(meta, matches)
    if cmp:
        wr = cmp["stats"]["win_rate"]
        if wr["delta"] is not None and abs(wr["delta"]) >= 8:
            up = wr["delta"] > 0
            items.append(
                (
                    abs(wr["delta"]),
                    {
                        "tone": "positive" if up else "negative",
                        "text": f"Win rate {'up' if up else 'down'} {abs(wr['delta']):.0f} pts over your last {cmp['window']} games "
                        f"({wr['previous']:.0f}% → {wr['recent']:.0f}%).",
                    },
                )
            )
        for key in meta.kpis:
            mt = meta.metric(key)
            s = cmp["stats"][key]
            if s["previous"] and s["delta"] is not None:
                pct = 100 * s["delta"] / abs(s["previous"])
                if abs(pct) >= 15:
                    better = (pct > 0) == mt.higher_is_better
                    items.append(
                        (
                            abs(pct) / 2,
                            {
                                "tone": "positive" if better else "negative",
                                "text": f"{mt.label} {'up' if pct > 0 else 'down'} {abs(pct):.0f}% over your last {cmp['window']} games "
                                f"({_fmt_value(mt.fmt, s['previous'])} → {_fmt_value(mt.fmt, s['recent'])}).",
                            },
                        )
                    )

    sessions = split_sessions(matches)
    early = [m for s in sessions for m in s[:2]]
    late = [m for s in sessions for m in s[4:]]
    if len(early) >= 10 and len(late) >= 10:
        e, lt = win_rate(early), win_rate(late)
        if e is not None and lt is not None and abs(e - lt) >= 10:
            items.append(
                (
                    abs(e - lt),
                    {
                        "tone": "tip" if lt < e else "positive",
                        "text": (
                            f"You win {e:.0f}% of your first 2 games in a session but {lt:.0f}% from game 5 on — consider shorter sessions."
                            if lt < e
                            else f"You warm up: {lt:.0f}% win rate from game 5 of a session vs {e:.0f}% in the first 2."
                        ),
                    },
                )
            )

    overall = win_rate(matches) or 0
    pool = [c for c in character_pool(meta, matches) if c["games"] >= 8]
    if pool:
        best = max(pool, key=lambda c: c["win_rate"] or 0)
        if (best["win_rate"] or 0) - overall >= 8:
            items.append(
                (
                    (best["win_rate"] or 0) - overall,
                    {
                        "tone": "positive",
                        "text": f"Best {meta.character_label.lower()}: {best['name']} — {best['win_rate']:.0f}% over {best['games']} games "
                        f"(vs {overall:.0f}% overall).",
                    },
                )
            )
        worst = min(pool, key=lambda c: c["win_rate"] or 0)
        if overall - (worst["win_rate"] or 0) >= 12 and worst is not best:
            items.append(
                (
                    overall - (worst["win_rate"] or 0),
                    {
                        "tone": "tip",
                        "text": f"{worst['name']} is costing you: {worst['win_rate']:.0f}% over {worst['games']} games.",
                    },
                )
            )

    mate = next(iter(teammates(matches, min_games=8)), None)
    if mate and (mate["win_rate"] or 0) - overall >= 8:
        items.append(
            (
                (mate["win_rate"] or 0) - overall,
                {
                    "tone": "positive",
                    "text": f"Best duo: {mate['win_rate']:.0f}% with {mate['name']} over {mate['games']} games.",
                },
            )
        )

    st = streaks(matches)["current"]
    if st["length"] >= 3:
        items.append(
            (
                st["length"] * 2,
                {"tone": "positive" if st["type"] == "win" else "negative", "text": f"On a {st['length']}-game {st['type']} streak."},
            )
        )

    items.sort(key=lambda x: -x[0])
    return [i for _, i in items[:max_items]] or [{"tone": "neutral", "text": "No strong patterns yet — keep playing and refresh."}]


def build_overview(meta: GameMeta, all_matches: Sequence[Match], window: int = 10) -> dict[str, Any]:
    ms = counted(all_matches)
    return {
        "summary": summarize(meta, ms),
        "compare": compare_windows(meta, ms),
        "streaks": streaks(ms),
        "characters": character_pool(meta, ms),
        "modes": by_mode(meta, ms),
        "roles": by_role(meta, ms),
        "maps": by_map(meta, ms),
        "teammates": teammates(ms),
        "sessions": session_analysis(ms),
        "win_factors": win_factors(meta, ms),
        "rank_history": rank_history(ms),
        "insights": insights(meta, ms),
        "trend": {
            "window": window,
            "dates": [m.date for m in ms],
            "win_rate": rolling(ms, lambda m: 100.0 if m.won else 0.0, window),
            **{k: rolling(ms, lambda m, k=k: m.metrics.get(k), window) for k in meta.trend_metrics},
        },
    }
