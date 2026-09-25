"""Plain-text summary for the terminal (``rlstats report``)."""

from __future__ import annotations

from typing import Any


def _fmt(v: float | None, digits: int = 2, suffix: str = "") -> str:
    return "—" if v is None else f"{v:.{digits}f}{suffix}"


def format_report(report: dict[str, Any], view: str = "all") -> str:
    v = report["views"].get(view) or report["views"]["all"]
    s = v["summary"]
    lines = [
        f"{report['player']} · {s['games']} games ({s['wins']}W–{s['losses']}L) · win rate {_fmt(s['win_rate'], 1, '%')}",
        "",
        f"  Per game   score {_fmt(s['avg']['score'], 0)}   goals {_fmt(s['avg']['goals'])}   assists {_fmt(s['avg']['assists'])}"
        f"   saves {_fmt(s['avg']['saves'])}   shooting {_fmt(s['shooting_pct'], 1, '%')}   MVP {_fmt(s['mvp_rate'], 0, '%')}",
    ]
    st = v["streaks"]
    n, kind = st["current"]["length"], st["current"]["type"]
    if n:
        plural = "" if n == 1 else ("es" if kind == "loss" else "s")
        lines.append(f"  Streak     {n} {kind}{plural} in a row · best {st['longest_win']}W · worst {st['longest_loss']}L")
    for r in v["ranks"]:
        lines.append(f"  Rank       {r['playlist_name']}: {r['current']} (peak {r['peak']})")
    if view == "all" and len(v["playlists"]) > 1:
        lines.append("")
        lines.append("  Playlists")
        for p in v["playlists"]:
            lines.append(f"    {p['name']:<20} {p['games']:>4} games   {_fmt(p['win_rate'], 1, '%'):>6} win rate")
    lines += ["", "  Insights"] + [f"    • {i['text']}" for i in v["insights"]]
    return "\n".join(lines)
