"""``clutch serve`` — run the API (and the built frontend) with uvicorn."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clutch", description="Multi-game stats hub")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the web app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--db", help="SQLite path (default $CLUTCH_DB or clutch.db)")
    serve.add_argument("--reload", action="store_true", help="auto-reload on code changes (development)")
    serve.add_argument("--desktop", action="store_true", help="enable local features: library, launcher, playtime, clipping")
    args = parser.parse_args(argv)

    load_dotenv(Path.cwd() / ".env")  # the installed app keeps keys next to its database
    load_dotenv()
    if args.db:
        os.environ["CLUTCH_DB"] = args.db
    if args.desktop:
        if args.host not in ("127.0.0.1", "localhost"):
            parser.error("--desktop only binds to 127.0.0.1: it can launch programs and delete files")
        os.environ["CLUTCH_DESKTOP"] = "1"
        from clutch.local.config import ALREADY_RUNNING, acquire_instance_lock, data_dir

        lock = acquire_instance_lock(data_dir() / "backend.lock")
        if lock is None:
            print("Another Clutch desktop backend is already running for this data folder.", file=sys.stderr, flush=True)
            return ALREADY_RUNNING
        # Who holds the lock (the lock file itself can't be read while locked on Windows).
        (data_dir() / "backend.pid").write_text(f"{os.getpid()} {args.port}", encoding="utf-8")
        if not os.environ.get("CLUTCH_TOKEN"):
            import secrets

            os.environ["CLUTCH_TOKEN"] = secrets.token_urlsafe(24)
            print(f"Desktop API token (send as X-Clutch-Token): {os.environ['CLUTCH_TOKEN']}", flush=True)
    import uvicorn

    uvicorn.run("clutch.app:create_app", factory=True, host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
