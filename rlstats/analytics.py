"""Performance analytics over a player's match history.

Everything here is pure (list of ``Match`` in, JSON-serializable dicts out),
so the CLI report, the dashboard and the tests all share one implementation.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from rlstats.parse import Match
from rlstats.ranks import rank_label

# Per-game stats averaged in summaries and compared across windows.
AVG_STATS = (
    "score",
    "goals",
    "assists",
    "saves",
    "shots",
    "demos_inflicted",
    "bpm",
    "avg_boost",
    "pct_zero_boost",
    "avg_speed",
    "pct_supersonic",
    "pct_high_air",
    "pct_behind_ball",
    "pct_defensive_third",
    "pct_offensive_third",
)

STAT_LABELS = {
    "score": "Score",
    "goals": "Goals",
    "assists": "Assists",
    "saves": "Saves",
    "shots": "Shots",
    "demos_inflicted": "Demos",
    "bpm": "Boost / min",
    "avg_boost": "Avg boost",
    "pct_zero_boost": "Time at 0 boost",
    "avg_speed": "Avg speed",
    "pct_supersonic": "Time supersonic",
    "pct_high_air": "Time in high air",
    "pct_behind_ball": "Time behind ball",
    "pct_defensive_third": "Time in defensive third",
    "pct_offensive_third": "Time in offensive third",
}

# (axis label, match attribute) for the playstyle radar: you vs your lobbies.
PLAYSTYLE_AXES = (
    ("Scoring", "goals"),
    ("Playmaking", "assists"),
    ("Shooting", "shots"),
    ("Defense", "saves"),
    ("Boost usage", "bpm"),
    ("Speed", "avg_speed"),
    ("Aerials", "pct_high_air"),
    ("Positioning", "pct_behind_ball"),
    ("Aggression", "demos_inflicted"),
)

# Features tested for "what separates your wins from your losses". Only
# process stats you can act on — outcome stats (goals, shots, score) would just
# restate the result. (attribute, label, unit, higher phrase, lower phrase)
WIN_FACTORS = (
    ("pct_zero_boost", "time at zero boost", "%", "more", "less"),
    ("pct_behind_ball", "time behind the ball", "%", "more", "less"),
    ("pct_supersonic", "time at supersonic", "%", "more", "less"),
    ("bpm", "boost used per minute", "", "more", "less"),
    ("avg_speed", "average speed", "", "higher", "lower"),
    ("pct_high_air", "time in the air", "%", "more", "less"),
    ("pct_offensive_third", "time in the offensive third", "%", "more", "less"),
    ("pct_defensive_third", "time in your defensive third", "%", "more", "less"),
    ("demos_inflicted", "demos", "", "more", "fewer"),
)

SESSION_GAP = timedelta(minutes=45)


def parse_date(value: str) -> datetime:
    """ballchasing dates are RFC 3339 (``Z`` or offset); naive dates are treated as UTC."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def sort_matches(matches: Sequence[Match]) -> list[Match]:
    return sorted(matches, key=lambda m: parse_date(m.date))


def _vals(matches: Sequence[Match], key: str) -> list[float]:
    return [v for v in (getattr(m, key) for m in matches) if v is not None]


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _round(v: float | None, nd: int = 2) -> float | None:
    return None if v is None else round(v, nd)


def win_rate(matches: Sequence[Match]) -> float | None:
    return 100 * sum(m.won for m in matches) / len(matches) if matches else None


# ── summaries ─────────────────────────────────────────────────────────────────


def summarize(matches: Sequence[Match]) -> dict[str, Any]:
    wins = sum(m.won for m in matches)
    shots, goals = sum(m.shots for m in matches), sum(m.goals for m in matches)
    ot = [m for m in matches if m.overtime]
    return {
        "games": len(matches),
        "wins": wins,
        "losses": len(matches) - wins,
        "win_rate": _round(win_rate(matches), 1),
        "mvp_rate": _round(100 * sum(m.mvp for m in matches) / len(matches), 1) if matches else None,
        "shooting_pct": _round(100 * goals / shots, 1) if shots else None,
        "overtime_games": len(ot),
        "overtime_win_rate": _round(win_rate(ot), 1),
        "avg": {k: _round(_mean(_vals(matches, k))) for k in AVG_STATS},
    }


def compare_windows(matches: Sequence[Match], window: int = 20) -> dict[str, Any] | None:
    """Last ``window`` games vs the ``window`` before them (needs 2x games)."""
    ordered = sort_matches(matches)
    if len(ordered) < 2 * window:
        window = len(ordered) // 2
    if window < 5:
        return None
    recent, previous = ordered[-window:], ordered[-2 * window : -window]
    out: dict[str, Any] = {"window": window, "stats": {}}
    for key in ("win_rate", *AVG_STATS):
        if key == "win_rate":
            r, p = win_rate(recent), win_rate(previous)
        else:
            r, p = _mean(_vals(recent, key)), _mean(_vals(previous, key))
        out["stats"][key] = {
            "recent": _round(r),
            "previous": _round(p),
            "delta": _round(r - p) if r is not None and p is not None else None,
        }
    return out


def rolling(matches: Sequence[Match], fn: Callable[[Match], float | None], window: int = 10) -> list[float | None]:
    """Trailing mean over up to ``window`` games (partial windows at the start)."""
    out: list[float | None] = []
    buf: list[float] = []
    for m in matches:
        v = fn(m)
        if v is not None:
            buf.append(v)
            if len(buf) > window:
                buf.pop(0)
        out.append(_round(_mean(buf)))
    return out


def streaks(matches: Sequence[Match]) -> dict[str, Any]:
    ordered = sort_matches(matches)
    longest = {True: 0, False: 0}
    run_val: bool | None = None
    run = 0
    for m in ordered:
        run = run + 1 if m.won == run_val else 1
        run_val = m.won
        longest[m.won] = max(longest[m.won], run)
    return {
        "current": {"type": ("win" if run_val else "loss") if run_val is not None else None, "length": run},
        "longest_win": longest[True],
        "longest_loss": longest[False],
    }


def by_playlist(matches: Sequence[Match]) -> list[dict[str, Any]]:
    groups: dict[str, list[Match]] = defaultdict(list)
    for m in matches:
        groups[m.playlist_id].append(m)
    rows = [
        {
            "id": pid,
            "name": ms[0].playlist_name,
            "games": len(ms),
            "win_rate": _round(win_rate(ms), 1),
            "avg_score": _round(_mean(_vals(ms, "score")), 1),
            "goals_per_game": _round(_mean(_vals(ms, "goals"))),
        }
        for pid, ms in groups.items()
    ]
    return sorted(rows, key=lambda r: -r["games"])


def teammates(matches: Sequence[Match], min_games: int = 3) -> list[dict[str, Any]]:
    """Win rate and your average score with each teammate you've queued with."""
    groups: dict[str, list[Match]] = defaultdict(list)
    names: dict[str, str] = {}
    for m in matches:
        for t in m.teammates:
            groups[t.key].append(m)
            names[t.key] = t.name  # latest name wins
    rows = [
        {
            "key": key,
            "name": names[key],
            "games": len(ms),
            "win_rate": _round(win_rate(ms), 1),
            "avg_score": _round(_mean(_vals(ms, "score")), 1),
            "goals_per_game": _round(_mean(_vals(ms, "goals"))),
        }
        for key, ms in groups.items()
        if len(ms) >= min_games
    ]
    return sorted(rows, key=lambda r: (-r["games"], -(r["win_rate"] or 0)))


def split_sessions(matches: Sequence[Match], gap: timedelta = SESSION_GAP) -> list[list[Match]]:
    """Group games into play sessions: a new session starts after ``gap`` of inactivity."""
    sessions: list[list[Match]] = []
    last_end: datetime | None = None
    for m in sort_matches(matches):
        start = parse_date(m.date)
        if last_end is None or start - last_end > gap:
            sessions.append([])
        sessions[-1].append(m)
        last_end = start + timedelta(seconds=m.duration or 0)
    return sessions


def _session_rank_change(session: Sequence[Match]) -> float | None:
    """Net rank movement in a session, summed per playlist (ranks differ across playlists)."""
    per_playlist: dict[str, list[float]] = defaultdict(list)
    for m in session:
        if m.rank_value is not None:
            per_playlist[m.playlist_id].append(m.rank_value)
    moves = [v[-1] - v[0] for v in per_playlist.values() if len(v) >= 2]
    return _round(sum(moves)) if moves else None


def session_analysis(matches: Sequence[Match], max_index: int = 8) -> dict[str, Any]:
    """Session list plus a "tilt curve": win rate by game number within a session."""
    sessions = split_sessions(matches)
    by_index: dict[int, list[Match]] = defaultdict(list)
    for s in sessions:
        for i, m in enumerate(s, 1):
            by_index[min(i, max_index)].append(m)
    curve = [
        {"game": f"{i}+" if i == max_index else str(i), "games": len(by_index[i]), "win_rate": _round(win_rate(by_index[i]), 1)}
        for i in range(1, max_index + 1)
        if by_index.get(i)
    ]
    rows = []
    for s in sessions:
        rows.append(
            {
                "start": s[0].date,
                "games": len(s),
                "wins": sum(m.won for m in s),
                "win_rate": _round(win_rate(s), 1),
                "avg_score": _round(_mean(_vals(s, "score")), 1),
                "rank_change": _session_rank_change(s),
            }
        )
    lengths = [len(s) for s in sessions]
    return {
        "count": len(sessions),
        "avg_games": _round(_mean(lengths), 1),
        "tilt_curve": curve,
        "recent": rows[-12:][::-1],
    }


def rank_history(matches: Sequence[Match]) -> list[dict[str, Any]]:
    """Rank over time per playlist (only points where the rank changed, plus the latest)."""
    groups: dict[str, list[Match]] = defaultdict(list)
    for m in sort_matches(matches):
        if m.rank_value is not None:
            groups[m.playlist_id].append(m)
    out = []
    for pid, ms in groups.items():
        points: list[dict[str, Any]] = []
        for m in ms:
            if not points or points[-1]["value"] != m.rank_value:
                points.append({"date": m.date, "value": m.rank_value, "label": m.rank_name or rank_label(m.rank_value)})
        if points[-1]["date"] != ms[-1].date:
            points.append(
                {"date": ms[-1].date, "value": ms[-1].rank_value, "label": ms[-1].rank_name or rank_label(ms[-1].rank_value)}
            )
        out.append(
            {
                "playlist_id": pid,
                "playlist_name": ms[0].playlist_name,
                "current": points[-1]["label"],
                "peak": rank_label(max(m.rank_value for m in ms)),  # type: ignore[type-var]
                "change": _round(points[-1]["value"] - points[0]["value"]),
                "points": points,
            }
        )
    return sorted(out, key=lambda r: -len(r["points"]))


def playstyle(matches: Sequence[Match]) -> list[dict[str, Any]]:
    """Each axis = your average / your lobbies' average x 100 (100 = lobby average)."""
    out = []
    for label, key in PLAYSTYLE_AXES:
        mine, lobby = [], []
        for m in matches:
            v, lv = getattr(m, key), m.lobby.get(key)
            if v is not None and lv is not None:
                mine.append(v)
                lobby.append(lv)
        you, them = _mean(mine), _mean(lobby)
        if you is None or not them:
            continue
        out.append({"axis": label, "stat": key, "you": _round(you), "lobby": _round(them), "index": round(100 * you / them)})
    return out


def win_factors(matches: Sequence[Match], min_side: int = 8) -> list[dict[str, Any]]:
    """Median-split each feature; how different is your win rate above vs below it?"""
    out = []
    for key, label, unit, more, less in WIN_FACTORS:
        pairs = [(getattr(m, key), m) for m in matches if getattr(m, key) is not None]
        if len(pairs) < 2 * min_side:
            continue
        median = statistics.median(v for v, _ in pairs)
        high = [m for v, m in pairs if v > median]
        low = [m for v, m in pairs if v <= median]
        if len(high) < min_side or len(low) < min_side:
            continue
        hi_wr, lo_wr = win_rate(high), win_rate(low)
        out.append(
            {
                "stat": key,
                "label": label,
                "unit": unit,
                "median": _round(median, 1),
                "high_win_rate": _round(hi_wr, 1),
                "low_win_rate": _round(lo_wr, 1),
                "difference": _round(hi_wr - lo_wr, 1),  # type: ignore[operator]
                "more": more,
                "less": less,
                "high_games": len(high),
                "low_games": len(low),
            }
        )
    return sorted(out, key=lambda r: -abs(r["difference"]))


# ── insights ──────────────────────────────────────────────────────────────────


def insights(matches: Sequence[Match], *, max_items: int = 7) -> list[dict[str, str]]:
    """Plain-English takeaways, strongest signal first. Each is backed by a sample-size guard."""
    ordered = sort_matches(matches)
    items: list[tuple[float, dict[str, str]]] = []
    if len(ordered) < 10:
        return [{"tone": "neutral", "text": f"Only {len(ordered)} games tracked — insights unlock at 10+ games."}]

    for f in win_factors(ordered)[:3]:
        diff = f["difference"]
        if abs(diff) < 8:
            continue
        better = f["more"] if diff > 0 else f["less"]
        best, worst = (f["high_win_rate"], f["low_win_rate"]) if diff > 0 else (f["low_win_rate"], f["high_win_rate"])
        items.append(
            (
                abs(diff),
                {
                    "tone": "tip",
                    "text": f"You win {best:.0f}% of games with {better} {f['label']} (split at {f['median']}{f['unit']}) "
                    f"vs {worst:.0f}% otherwise.",
                },
            )
        )

    cmp = compare_windows(ordered)
    if cmp:
        wr = cmp["stats"]["win_rate"]
        if wr["delta"] is not None and abs(wr["delta"]) >= 8:
            up = wr["delta"] > 0
            items.append(
                (
                    abs(wr["delta"]),
                    {
                        "tone": "positive" if up else "negative",
                        "text": f"Win rate is {'up' if up else 'down'} {abs(wr['delta']):.0f} pts over your last {cmp['window']} games "
                        f"({wr['previous']:.0f}% → {wr['recent']:.0f}%).",
                    },
                )
            )
        for key in ("score", "goals", "saves", "assists"):
            s = cmp["stats"][key]
            if s["previous"] and s["delta"] is not None:
                pct = 100 * s["delta"] / s["previous"]
                if abs(pct) >= 15:
                    items.append(
                        (
                            abs(pct) / 2,
                            {
                                "tone": "positive" if pct > 0 else "negative",
                                "text": f"{STAT_LABELS[key]} per game {'up' if pct > 0 else 'down'} {abs(pct):.0f}% "
                                f"over your last {cmp['window']} games ({s['previous']} → {s['recent']}).",
                            },
                        )
                    )

    sessions = split_sessions(ordered)
    early = [m for s in sessions for m in s[:3]]
    late = [m for s in sessions for m in s[5:]]
    if len(early) >= 10 and len(late) >= 10:
        e, lt = win_rate(early), win_rate(late)
        if e is not None and lt is not None and abs(e - lt) >= 10:
            tired = lt < e
            items.append(
                (
                    abs(e - lt),
                    {
                        "tone": "tip" if tired else "positive",
                        "text": (
                            f"You win {e:.0f}% of your first 3 games in a session but {lt:.0f}% from game 6 on — "
                            "consider shorter sessions."
                            if tired
                            else f"You warm up: {lt:.0f}% win rate from game 6 of a session vs {e:.0f}% in the first 3."
                        ),
                    },
                )
            )

    best_mate = next((t for t in teammates(ordered, min_games=8) if t["win_rate"] is not None), None)
    overall = win_rate(ordered) or 0
    if best_mate and best_mate["win_rate"] - overall >= 8:
        items.append(
            (
                best_mate["win_rate"] - overall,
                {
                    "tone": "positive",
                    "text": f"Best duo: {best_mate['win_rate']:.0f}% win rate with {best_mate['name']} over {best_mate['games']} games "
                    f"(vs {overall:.0f}% overall).",
                },
            )
        )

    playlists = [p for p in by_playlist(ordered) if p["games"] >= 10]
    if len(playlists) >= 2:
        best = max(playlists, key=lambda p: p["win_rate"] or 0)
        worst = min(playlists, key=lambda p: p["win_rate"] or 0)
        if (best["win_rate"] or 0) - (worst["win_rate"] or 0) >= 10:
            items.append(
                (
                    (best["win_rate"] or 0) - (worst["win_rate"] or 0),
                    {
                        "tone": "neutral",
                        "text": f"Strongest playlist: {best['name']} ({best['win_rate']:.0f}%); weakest: {worst['name']} ({worst['win_rate']:.0f}%).",
                    },
                )
            )

    st = streaks(ordered)["current"]
    if st["length"] >= 3:
        items.append(
            (
                st["length"] * 2,
                {
                    "tone": "positive" if st["type"] == "win" else "negative",
                    "text": f"On a {st['length']}-game {st['type']} streak.",
                },
            )
        )

    for axis in playstyle(ordered):
        if axis["index"] >= 125:
            items.append(
                (
                    (axis["index"] - 100) / 3,
                    {
                        "tone": "positive",
                        "text": f"Standout trait — {axis['axis'].lower()}: {axis['index'] - 100}% above your lobbies' average.",
                    },
                )
            )
        elif axis["index"] <= 75:
            items.append(
                (
                    (100 - axis["index"]) / 3,
                    {
                        "tone": "tip",
                        "text": f"Growth area — {axis['axis'].lower()}: {100 - axis['index']}% below your lobbies' average.",
                    },
                )
            )

    items.sort(key=lambda x: -x[0])
    return [i for _, i in items[:max_items]] or [
        {"tone": "neutral", "text": "No strong patterns yet — keep playing and re-sync."}
    ]


# ── report ────────────────────────────────────────────────────────────────────


def build_view(matches: Sequence[Match], rolling_window: int = 10) -> dict[str, Any]:
    ordered = sort_matches(matches)
    return {
        "summary": summarize(ordered),
        "compare": compare_windows(ordered),
        "streaks": streaks(ordered),
        "playlists": by_playlist(ordered),
        "teammates": teammates(ordered),
        "sessions": session_analysis(ordered),
        "ranks": rank_history(ordered),
        "playstyle": playstyle(ordered),
        "win_factors": win_factors(ordered),
        "insights": insights(ordered),
        "trend": {
            "dates": [m.date for m in ordered],
            "win_rate": rolling(ordered, lambda m: 100.0 if m.won else 0.0, rolling_window),
            "score": rolling(ordered, lambda m: m.score, rolling_window),
            "goals": rolling(ordered, lambda m: m.goals, rolling_window),
            "assists": rolling(ordered, lambda m: m.assists, rolling_window),
            "saves": rolling(ordered, lambda m: m.saves, rolling_window),
            "bpm": rolling(ordered, lambda m: m.bpm, rolling_window),
            "pct_zero_boost": rolling(ordered, lambda m: m.pct_zero_boost, rolling_window),
            "pct_behind_ball": rolling(ordered, lambda m: m.pct_behind_ball, rolling_window),
            "window": rolling_window,
        },
    }


MATCH_LOG_FIELDS = (
    "id",
    "date",
    "playlist_id",
    "playlist_name",
    "won",
    "team_goals",
    "opponent_goals",
    "overtime",
    "score",
    "goals",
    "assists",
    "saves",
    "shots",
    "mvp",
    "demos_inflicted",
    "bpm",
    "pct_zero_boost",
    "rank_name",
    "map_name",
    "car",
)


def build_report(matches: Sequence[Match], player: str, *, synced_at: str | None = None) -> dict[str, Any]:
    ordered = sort_matches(matches)
    playlists = by_playlist(ordered)
    views = {"all": build_view(ordered)}
    for p in playlists:
        if p["games"] >= 5:
            views[p["id"]] = build_view([m for m in ordered if m.playlist_id == p["id"]])
    return {
        "player": player,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "synced_at": synced_at,
        "first_date": ordered[0].date if ordered else None,
        "last_date": ordered[-1].date if ordered else None,
        "playlists": [{"id": p["id"], "name": p["name"], "games": p["games"]} for p in playlists if p["id"] in views],
        "views": views,
        "matches": [
            {**{k: getattr(m, k) for k in MATCH_LOG_FIELDS}, "link": m.link, "teammates": [t.name for t in m.teammates]}
            for m in reversed(ordered)
        ],
        "stat_labels": STAT_LABELS,
    }
