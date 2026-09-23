"""``rlstats`` command line: sync, dashboard, report, export, demo, whoami."""

from __future__ import annotations

import argparse
import csv
import sys
import webbrowser
from pathlib import Path

from rlstats import __version__
from rlstats.analytics import MATCH_LOG_FIELDS, build_report, sort_matches
from rlstats.client import BallchasingClient, BallchasingError
from rlstats.config import Settings
from rlstats.dashboard import fetch_chartjs, write_dashboard
from rlstats.demo import DEMO_PLAYER, generate_replays
from rlstats.parse import Match, ParseError, PlayerRef, parse_replay
from rlstats.report import format_report
from rlstats.store import ReplayStore
from rlstats.sync import sync


def _identity(settings: Settings, store: ReplayStore) -> tuple[PlayerRef, str]:
    """Explicit settings win; otherwise the identity remembered from the last sync."""
    player_id = settings.player_id or store.get_meta("player_id")
    name = settings.player_name or store.get_meta("player_name")
    return PlayerRef.parse(player_id, name), name or player_id or "Player"


def load_matches(store: ReplayStore, who: PlayerRef) -> tuple[list[Match], int]:
    """Parse every stored replay; returns (matches, replays the player wasn't found in)."""
    matches, missing = [], 0
    for raw in store.iter_raw():
        try:
            m = parse_replay(raw, who)
        except ParseError:
            continue
        if m is None:
            missing += 1
        else:
            matches.append(m)
    return sort_matches(matches), missing


def cmd_sync(args: argparse.Namespace, settings: Settings) -> int:
    client = BallchasingClient(settings.api_key or "")
    with ReplayStore(args.db or settings.db_path) as store:
        if not (settings.player_id or settings.player_name or store.get_meta("player_id")):
            me = client.whoami()
            if me.get("steam_id"):
                store.set_meta("player_id", f"steam:{me['steam_id']}")
            if me.get("name"):
                store.set_meta("player_name", me["name"])
            print(f"Tracking the API key's owner: {me.get('name', '?')} (set RL_PLAYER_ID to track someone else)")
        player_filter = None
        if args.source == "player":
            player_filter = settings.player_id or store.get_meta("player_id")
            if not player_filter:
                print("--source player needs RL_PLAYER_ID (platform:id)", file=sys.stderr)
                return 2
        print(f"Syncing into {store.path} ...")
        result = sync(client, store, player_filter=player_filter, max_new=args.max, full=args.full)
        print(
            f"Done: {result.downloaded} new replays ({result.already_had} already stored, "
            f"{result.pending} still processing, {len(result.failed)} failed). Total stored: {store.count()}."
        )
    if not args.no_dashboard:
        return cmd_dashboard(args, settings)
    return 0


def cmd_dashboard(args: argparse.Namespace, settings: Settings) -> int:
    with ReplayStore(args.db or settings.db_path) as store:
        who, name = _identity(settings, store)
        if not who:
            print("No player configured: set RL_PLAYER_ID or RL_PLAYER_NAME (or run `rlstats sync` first).", file=sys.stderr)
            return 2
        matches, missing = load_matches(store, who)
        report = build_report(matches, name, synced_at=store.get_meta("last_sync"))
    inline = fetch_chartjs() if getattr(args, "offline", False) else None
    out = write_dashboard(report, args.output or settings.output, inline_chartjs=inline)
    note = f" ({missing} stored replays didn't include you)" if missing else ""
    print(f"Wrote {out} — {len(matches)} matches{note}.")
    if getattr(args, "open", False):
        webbrowser.open(out.resolve().as_uri())
    return 0


def cmd_report(args: argparse.Namespace, settings: Settings) -> int:
    with ReplayStore(args.db or settings.db_path) as store:
        who, name = _identity(settings, store)
        matches, _ = load_matches(store, who)
    if not matches:
        print("No matches found — run `rlstats sync` (or `rlstats demo`) first.")
        return 1
    print(format_report(build_report(matches, name), args.playlist))
    return 0


def cmd_export(args: argparse.Namespace, settings: Settings) -> int:
    with ReplayStore(args.db or settings.db_path) as store:
        who, _ = _identity(settings, store)
        matches, _ = load_matches(store, who)
    fields = [
        *MATCH_LOG_FIELDS,
        "avg_boost",
        "avg_speed",
        "pct_supersonic",
        "pct_high_air",
        "pct_behind_ball",
        "pct_defensive_third",
        "pct_offensive_third",
        "duration",
        "team_size",
    ]
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([*fields, "teammates"])
        for m in matches:
            w.writerow([getattr(m, k) for k in fields] + ["; ".join(t.name for t in m.teammates)])
    print(f"Exported {len(matches)} matches to {args.output}")
    return 0


def cmd_demo(args: argparse.Namespace, settings: Settings) -> int:
    db = Path(args.db or "demo.db")
    if db.exists():
        db.unlink()
    with ReplayStore(db) as store:
        store.upsert_many(generate_replays(games=args.games, seed=args.seed))
        store.set_meta("player_id", f"{DEMO_PLAYER['platform']}:{DEMO_PLAYER['id']}")
        store.set_meta("player_name", DEMO_PLAYER["name"])
        store.set_meta("last_sync", "2026-09-21T23:59:00+00:00")
    demo_settings = Settings(None, None, None, db, Path(args.output or "demo_dashboard.html"))
    args.db, args.output = str(db), str(demo_settings.output)
    return cmd_dashboard(args, demo_settings)


def cmd_whoami(args: argparse.Namespace, settings: Settings) -> int:
    me = BallchasingClient(settings.api_key or "").whoami()
    print(f"{me.get('name', '?')} · steam_id={me.get('steam_id', '?')} · account type={me.get('type', '?')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rlstats", description="Rocket League performance analytics from ballchasing.com replays.")
    p.add_argument("--version", action="version", version=f"rlstats {__version__}")
    p.add_argument("--db", help="SQLite database path (default: $RLSTATS_DB or rlstats.db)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("sync", help="download new replays, then rebuild the dashboard")
    s.add_argument("--full", action="store_true", help="walk the whole replay list instead of stopping at known replays")
    s.add_argument("--max", type=int, default=500, help="max new replays to download (default 500)")
    s.add_argument(
        "--source",
        choices=("mine", "player"),
        default="mine",
        help="mine = replays your account uploaded; player = every public replay with RL_PLAYER_ID",
    )
    s.add_argument("--no-dashboard", action="store_true")
    s.add_argument("-o", "--output")
    s.add_argument("--offline", action="store_true", help="embed Chart.js so the HTML works without internet")
    s.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    s.set_defaults(func=cmd_sync)

    d = sub.add_parser("dashboard", help="build the HTML dashboard from stored replays")
    d.add_argument("-o", "--output")
    d.add_argument("--offline", action="store_true", help="embed Chart.js so the HTML works without internet")
    d.add_argument("--open", action="store_true")
    d.set_defaults(func=cmd_dashboard)

    r = sub.add_parser("report", help="print a summary and insights to the terminal")
    r.add_argument("--playlist", default="all", help="playlist id, e.g. ranked-doubles")
    r.set_defaults(func=cmd_report)

    e = sub.add_parser("export", help="export parsed matches to CSV")
    e.add_argument("-o", "--output", default="matches.csv")
    e.set_defaults(func=cmd_export)

    m = sub.add_parser("demo", help="generate a synthetic season and build a demo dashboard (no API key needed)")
    m.add_argument("--games", type=int, default=320)
    m.add_argument("--seed", type=int, default=7)
    m.add_argument("-o", "--output")
    m.add_argument("--offline", action="store_true", help="embed Chart.js so the HTML works without internet")
    m.add_argument("--open", action="store_true")
    m.set_defaults(func=cmd_demo)

    w = sub.add_parser("whoami", help="show the ballchasing account that owns the API key")
    w.set_defaults(func=cmd_whoami)
    return p


def main(argv: list[str] | None = None) -> int:
    # Insight text uses arrows and dashes; piped output on Windows defaults to
    # cp1252, so force UTF-8 there (real consoles already write Unicode).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8" if not stream.isatty() else stream.encoding, errors="replace")
    args = build_parser().parse_args(argv)
    settings = Settings.load()
    try:
        return int(args.func(args, settings) or 0)
    except BallchasingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
