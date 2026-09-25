"""Competitive rank ladder helpers.

ballchasing reports ranks as ``{"tier": 13, "division": 2, "name": "Diamond I Div 2"}``.
Tier 0 is unranked, 1-3 Bronze I-III ... 19-21 Grand Champion I-III, 22 SSL.
Divisions are 1-4 within a tier. ``rank_value`` flattens both into one number
so rank progression can be charted on a single axis.
"""

from __future__ import annotations

_GROUPS = ["Bronze", "Silver", "Gold", "Platinum", "Diamond", "Champion", "Grand Champion"]
_ROMAN = ["I", "II", "III"]


def tier_name(tier: int | None) -> str:
    if not tier:
        return "Unranked"
    if tier >= 22:
        return "Supersonic Legend"
    group = _GROUPS[(tier - 1) // 3]
    return f"{group} {_ROMAN[(tier - 1) % 3]}"


def rank_value(tier: int | None, division: int | None) -> float | None:
    """Tier plus a quarter step per division: Diamond I Div 1 -> 13.0, Div 4 -> 13.75."""
    if not tier:
        return None
    div = min(max(division or 1, 1), 4)
    return tier + (div - 1) / 4


def rank_label(value: float | None) -> str:
    if value is None:
        return "Unranked"
    tier = int(value)
    if tier >= 22:
        return tier_name(tier)
    division = int(round((value - tier) * 4)) + 1
    return f"{tier_name(tier)} Div {division}"
