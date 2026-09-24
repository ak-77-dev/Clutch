"""Chess: Chess.com (https://www.chess.com/news/view/published-data-api) and Lichess
(https://lichess.org/api). Both are public; no API key needed.

A "match" is one game. The "character" is the opening family (so the pool view
shows which openings win for you), the role is the colour you played, and the
mode is the time control. Ratings stand in for ranks.

- Chess.com: ``/pub/player/{u}`` + ``/stats`` for the profile and ratings, then the
  monthly archives (``/games/archives`` -> ``/games/YYYY/MM``), newest first. Accuracy
  is only there for games someone reviewed.
- Lichess: ``/api/user/{u}`` for ratings, ``/api/games/user/{u}`` (NDJSON) for games,
  with engine analysis (accuracy, ACPL, blunders) when the game was analysed.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from clutch.games.base import GameProvider
from clutch.http import JsonClient, NotFound, SlidingWindowLimiter
from clutch.models import GameMeta, Match, Metric, Profile, ScoreRow

CHESSCOM = "https://api.chess.com/pub"
LICHESS = "https://lichess.org"
PIECES = "https://lichess1.org/assets/piece/cburnett/{}.svg"
PIECE = PIECES.replace("{}", "{}K")  # a king in your colour stands in for a portrait
TC_PIECE = {"bullet": "wP", "blitz": "wN", "rapid": "wR", "classical": "wQ", "daily": "wK", "correspondence": "wK"}
TIME_CLASSES = ("bullet", "blitz", "rapid", "classical", "daily", "correspondence")
DRAWS = {"agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"}
FAMILY_END = {"Opening", "Defense", "Defence", "Gambit", "Game", "Attack", "System", "Countergambit"}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def opening_family(name: str | None) -> str:
    """'Sicilian Defense: Najdorf Variation' -> 'Sicilian Defense'; chess.com URL slugs too."""
    if not name:
        return "Unknown opening"
    if "/openings/" in name:
        slug = name.rsplit("/openings/", 1)[1].split("...")[0]
        words = [w for w in slug.split("-") if w and not re.match(r"^\d", w)]
        family: list[str] = []
        for w in words:
            family.append(w)
            if w in FAMILY_END:
                break
        return " ".join(family[:5]) or "Unknown opening"
    return name.split(":")[0].split(",")[0].strip()


def player_id(profile: Profile) -> str:
    """The username in game records. Demo profiles are keyed "demo:<name>" so they can
    never share storage with a real account of the same name."""
    return profile.name.lower() if getattr(profile, "demo", False) else profile.key


def pgn_result(outcome: str, colour: str) -> str:
    """The game result in chess notation (White's score first), whichever side you played."""
    if outcome == "draw":
        return "½–½"
    if outcome == "remake":
        return "—"
    white_won = (outcome == "win") == (colour == "white")
    return "1–0" if white_won else "0–1"


def _rating_rank(queue: str, rating: int | None, games: int | None = None) -> dict[str, Any]:
    return {
        "queue": queue,
        "label": str(rating) if rating else "Unrated",
        "tier": queue.lower(),
        "lp": None,
        "value": float(rating) if rating else None,
        "icon": PIECES.format(TC_PIECE.get(queue.lower(), "wK")),
        "wins": None,
        "losses": None,
        "games": games,
    }


# ── Chess.com ────────────────────────────────────────────────────────────────

CHESSCOM_META = GameMeta(
    id="chesscom",
    name="Chess.com",
    character_label="Opening",
    search_hint="Chess.com username",
    accent="#81b64c",
    metrics=(
        Metric("rating", "Rating", "int"),
        Metric("rating_edge", "Rating vs opponent", "int", short="±ELO"),
        Metric("accuracy", "Accuracy", "pct", short="ACC"),
        Metric("moves", "Moves", "int"),
        Metric("minutes", "Game length (min)", "float1", short="MIN"),
    ),
    kpis=("accuracy", "rating", "rating_edge", "moves"),
    trend_metrics=("rating", "accuracy"),
    factor_metrics=("accuracy", "rating_edge"),  # length and move count follow from the result, so they'd only restate it
    card_metrics=("accuracy", "rating", "rating_edge", "moves"),
)


def _pgn_tags(pgn: str) -> dict[str, str]:
    return dict(re.findall(r'^\[(\w+) "([^"]*)"\]', pgn or "", flags=re.M))


def slim_chesscom(game: dict[str, Any]) -> dict[str, Any]:
    """Drop the PGN / TCN / FEN (the bulk of each game), keep what the stats need."""
    tags = _pgn_tags(game.get("pgn", ""))
    fen = game.get("fen") or ""
    moves = int(fen.split()[-1]) if fen.split() and fen.split()[-1].isdigit() else None
    start = None
    if tags.get("UTCDate") and tags.get("UTCTime"):
        start = datetime.strptime(f"{tags['UTCDate']} {tags['UTCTime']}", "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    keep = ("url", "uuid", "end_time", "rated", "accuracies", "time_class", "time_control", "rules", "white", "black", "eco")
    out = {k: game.get(k) for k in keep}
    out.update(
        {"start_time": start, "moves": moves, "termination": tags.get("Termination"), "opening": tags.get("ECOUrl") or game.get("eco")}
    )
    return out


class ChessComProvider(GameProvider):
    meta = CHESSCOM_META

    def __init__(self, client: JsonClient | None = None) -> None:
        # The published-data API asks for serial requests; a few per second is plenty.
        self.client = client or JsonClient(limiter=SlidingWindowLimiter([(3, 1.0)]))
        self._games: dict[str, dict[str, Any]] = {}

    def configured(self) -> bool:
        return True

    def resolve(self, query: str) -> Profile:
        name = query.strip().lstrip("@").lower()
        if not re.fullmatch(r"[a-z0-9_-]{3,25}", name):
            raise NotFound("Chess.com usernames are 3–25 letters, digits, - or _")
        return self.refresh_profile(Profile(game="chesscom", key=name, name=name))

    def refresh_profile(self, profile: Profile) -> Profile:
        p = self.client.get(f"{CHESSCOM}/player/{profile.key}")
        stats = self.client.get(f"{CHESSCOM}/player/{profile.key}/stats") or {}
        profile.name = p.get("username") or profile.name
        profile.icon = p.get("avatar")
        profile.region = (p.get("country") or "").rsplit("/", 1)[-1] or None
        ranks = []
        for tc in ("rapid", "blitz", "bullet", "daily"):
            s = stats.get(f"chess_{tc}")
            if s:
                rec = s.get("record") or {}
                entry = _rating_rank(tc.title(), (s.get("last") or {}).get("rating"))
                entry.update(wins=rec.get("win"), losses=rec.get("loss"))
                ranks.append(entry)
        # Most-played time control first: that's the one the rest of the app treats as "your rank".
        ranks.sort(key=lambda r: -((r["wins"] or 0) + (r["losses"] or 0)))
        profile.ranks = ranks
        return profile

    def list_match_ids(self, profile: Profile, limit: int) -> list[str]:
        archives = (self.client.get(f"{CHESSCOM}/player/{profile.key}/games/archives") or {}).get("archives") or []
        ids: list[str] = []
        for url in reversed(archives[-6:]):  # newest months first
            games = (self.client.get(url) or {}).get("games") or []
            for g in sorted(games, key=lambda g: g.get("end_time") or 0, reverse=True):
                slim = slim_chesscom(g)
                gid = self.match_id_of(slim)
                self._games[gid] = slim
                ids.append(gid)
                if len(ids) >= limit:
                    return ids
        return ids

    def fetch_match(self, profile: Profile, match_id: str) -> dict[str, Any]:
        if match_id not in self._games:  # archives are fetched by list_match_ids
            self.list_match_ids(profile, 500)
        try:
            return self._games[match_id]
        except KeyError:
            raise NotFound("That game isn't in the player's recent archives") from None

    def match_id_of(self, raw: dict[str, Any]) -> str:
        return str(raw.get("uuid") or (raw.get("url") or "").rsplit("/", 1)[-1])

    def match_date(self, raw: dict[str, Any]) -> str:
        return _iso(raw.get("start_time") or raw.get("end_time") or 0)

    def parse(self, raw: dict[str, Any], profile: Profile) -> Match | None:
        who = player_id(profile)
        colour = next((c for c in ("white", "black") if (raw.get(c) or {}).get("username", "").lower() == who), None)
        if colour is None:
            return None
        me, opp = raw[colour], raw["black" if colour == "white" else "white"]
        result = me.get("result")
        outcome = "win" if result == "win" else "draw" if result in DRAWS else "loss"
        tc = raw.get("time_class") or "rapid"
        rules = raw.get("rules") or "chess"
        mode = tc.title() if rules == "chess" else f"{rules.title()} · {tc.title()}"
        start, end = raw.get("start_time"), raw.get("end_time")
        seconds = float(end - start) if start and end and end >= start else 0.0
        mine, theirs = me.get("rating"), opp.get("rating")
        accuracy = (raw.get("accuracies") or {}).get(colour)
        score = pgn_result(outcome, colour)
        return Match(
            game="chesscom",
            id=self.match_id_of(raw),
            date=self.match_date(raw),
            mode=mode + ("" if raw.get("rated", True) else " (casual)"),
            duration_s=seconds,
            result=outcome,
            character=opening_family(raw.get("opening")),
            character_icon=PIECE.format("w" if colour == "white" else "b"),
            role=colour.title(),
            metrics={
                "rating": mine,
                "rating_edge": (mine - theirs) if mine and theirs else None,
                "accuracy": accuracy,
                "moves": raw.get("moves"),
                "minutes": round(seconds / 60, 1) if seconds else None,
            },
            rank_label=f"{tc.title()} {mine}" if raw.get("rated", True) and mine else None,
            rank_value=float(mine) if raw.get("rated", True) and mine and rules == "chess" else None,
            score_line=score,
            teammates=[],
            scoreboard=[
                ScoreRow(
                    name=p.get("username", "?"),
                    team=c.title(),
                    character=c.title(),
                    character_icon=PIECE.format(c[0]),
                    is_self=c == colour,
                    rank=str(p.get("rating") or ""),
                    stats={
                        "Rating": p.get("rating") or 0,
                        "Accuracy": (raw.get("accuracies") or {}).get(c) or "—",
                        "Result": p.get("result"),
                    },
                    extra={"won": p.get("result") == "win"},
                )
                for c, p in (("white", raw["white"]), ("black", raw["black"]))
            ],
            link=raw.get("url"),
        )

    def demo_profile(self) -> Profile:
        from clutch.games.demo_more import CHESSCOM_DEMO_PROFILE, chess_demo_ranks

        p = Profile(**CHESSCOM_DEMO_PROFILE)
        p.ranks = chess_demo_ranks(self.demo_matches(), p.name.lower(), "chesscom")  # ratings that match the history
        return p

    def demo_matches(self) -> list[dict[str, Any]]:
        from clutch.games.demo_more import chesscom_matches

        return chesscom_matches()


# ── Lichess ──────────────────────────────────────────────────────────────────

LICHESS_META = GameMeta(
    id="lichess",
    name="Lichess",
    character_label="Opening",
    search_hint="Lichess username",
    accent="#d9d9d9",
    metrics=(
        Metric("rating", "Rating", "int"),
        Metric("rating_change", "Rating change", "int", short="±"),
        Metric("rating_edge", "Rating vs opponent", "int", short="±ELO"),
        Metric("accuracy", "Accuracy", "pct", short="ACC"),
        Metric("acpl", "Avg. centipawn loss", "int", higher_is_better=False, short="ACPL"),
        Metric("blunders", "Blunders", "float1", higher_is_better=False, short="??"),
        Metric("mistakes", "Mistakes", "float1", higher_is_better=False, short="?"),
        Metric("moves", "Moves", "int"),
        Metric("minutes", "Game length (min)", "float1", short="MIN"),
    ),
    kpis=("accuracy", "acpl", "blunders", "rating"),
    trend_metrics=("rating", "accuracy", "acpl"),
    factor_metrics=("accuracy", "acpl", "blunders", "mistakes", "rating_edge"),
    card_metrics=("accuracy", "rating_change", "acpl", "blunders"),
)
LICHESS_VOID_STATUS = {"aborted", "noStart", "unknownFinish", "created", "started"}


def slim_lichess(game: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in game.items() if k not in ("moves", "clock", "clocks", "analysis", "pgn")}
    out["plies"] = len((game.get("moves") or "").split())
    return out


class LichessProvider(GameProvider):
    meta = LICHESS_META

    def __init__(self, client: JsonClient | None = None) -> None:
        # Lichess asks API users to make one request at a time.
        self.client = client or JsonClient(limiter=SlidingWindowLimiter([(1, 1.0)]), timeout=30)
        self._games: dict[str, dict[str, Any]] = {}

    def configured(self) -> bool:
        return True

    def resolve(self, query: str) -> Profile:
        name = query.strip().lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9_-]{2,30}", name):
            raise NotFound("Lichess usernames are 2–30 letters, digits, - or _")
        return self.refresh_profile(Profile(game="lichess", key=name.lower(), name=name))

    def refresh_profile(self, profile: Profile) -> Profile:
        u = self.client.get(f"{LICHESS}/api/user/{profile.key}")
        if u.get("disabled") or u.get("closed"):
            raise NotFound("That Lichess account is closed.")
        profile.name = u.get("username") or profile.name
        profile.tag = u.get("title")
        perfs = u.get("perfs") or {}
        ranks = [
            _rating_rank(tc.title(), perfs[tc].get("rating"), perfs[tc].get("games"))
            for tc in ("bullet", "blitz", "rapid", "classical", "correspondence")
            if (perfs.get(tc) or {}).get("games")
        ]
        ranks.sort(key=lambda r: -(r.get("games") or 0))
        profile.ranks = ranks
        return profile

    def list_match_ids(self, profile: Profile, limit: int) -> list[str]:
        games = self.client.get_ndjson(
            f"{LICHESS}/api/games/user/{profile.key}",
            {"max": min(limit, 300), "opening": "true", "accuracy": "true", "moves": "true", "clocks": "false", "evals": "false"},
        )
        for g in games:
            self._games[g["id"]] = slim_lichess(g)
        return [g["id"] for g in games]

    def fetch_match(self, profile: Profile, match_id: str) -> dict[str, Any]:
        if match_id in self._games:
            return self._games[match_id]
        game = self.client.get(
            f"{LICHESS}/game/export/{match_id}", {"opening": "true", "accuracy": "true"}, headers={"Accept": "application/json"}
        )
        return slim_lichess(game)

    def match_id_of(self, raw: dict[str, Any]) -> str:
        return str(raw["id"])

    def match_date(self, raw: dict[str, Any]) -> str:
        return _iso((raw.get("createdAt") or 0) / 1000)

    def parse(self, raw: dict[str, Any], profile: Profile) -> Match | None:
        players = raw.get("players") or {}
        who = player_id(profile)
        colour = next((c for c in ("white", "black") if ((players.get(c) or {}).get("user") or {}).get("id") == who), None)
        if colour is None:
            return None
        me, opp = players[colour], players["black" if colour == "white" else "white"]
        status = raw.get("status")
        if status in LICHESS_VOID_STATUS:
            outcome = "remake"
        elif raw.get("winner"):
            outcome = "win" if raw["winner"] == colour else "loss"
        else:  # no winner: draw, stalemate, or a timeout against insufficient material
            outcome = "draw"
        seconds = max(0.0, ((raw.get("lastMoveAt") or 0) - (raw.get("createdAt") or 0)) / 1000)
        analysis = me.get("analysis") or {}
        mine, theirs = me.get("rating"), opp.get("rating")
        speed = raw.get("speed") or raw.get("perf") or "blitz"
        variant = raw.get("variant") or "standard"
        mode = speed.title() if variant == "standard" else f"{variant.title()} · {speed.title()}"
        rated = raw.get("rated", False)
        return Match(
            game="lichess",
            id=self.match_id_of(raw),
            date=self.match_date(raw),
            mode=mode + ("" if rated else " (casual)"),
            duration_s=seconds,
            result=outcome,
            character=opening_family((raw.get("opening") or {}).get("name")),
            character_icon=PIECE.format("w" if colour == "white" else "b"),
            role=colour.title(),
            metrics={
                "rating": mine,
                "rating_change": me.get("ratingDiff"),
                "rating_edge": (mine - theirs) if mine and theirs else None,
                "accuracy": analysis.get("accuracy"),
                "acpl": analysis.get("acpl"),
                "blunders": analysis.get("blunder"),
                "mistakes": analysis.get("mistake"),
                "moves": (raw.get("plies") or 0) // 2 + (raw.get("plies") or 0) % 2 or None,
                "minutes": round(seconds / 60, 1) if seconds else None,
            },
            rank_label=f"{speed.title()} {mine}" if rated and mine else None,
            rank_value=float(mine) if rated and mine and variant == "standard" else None,
            score_line=pgn_result(outcome, colour),
            teammates=[],
            scoreboard=[
                ScoreRow(
                    name=((p.get("user") or {}).get("name")) or "Anonymous",
                    team=c.title(),
                    character=c.title(),
                    character_icon=PIECE.format(c[0]),
                    is_self=c == colour,
                    rank=str(p.get("rating") or ""),
                    stats={
                        "Rating": p.get("rating") or 0,
                        "±": p.get("ratingDiff") or 0,
                        "Accuracy": (p.get("analysis") or {}).get("accuracy") or "—",
                        "Blunders": (p.get("analysis") or {}).get("blunder", "—"),
                    },
                    extra={"won": raw.get("winner") == c},
                )
                for c, p in (("white", players.get("white") or {}), ("black", players.get("black") or {}))
            ],
            link=f"{LICHESS}/{raw['id']}",
        )

    def demo_profile(self) -> Profile:
        from clutch.games.demo_more import LICHESS_DEMO_PROFILE, chess_demo_ranks

        p = Profile(**LICHESS_DEMO_PROFILE)
        p.ranks = chess_demo_ranks(self.demo_matches(), p.name.lower(), "lichess")  # ratings that match the history
        return p

    def demo_matches(self) -> list[dict[str, Any]]:
        from clutch.games.demo_more import lichess_matches

        return lichess_matches()
