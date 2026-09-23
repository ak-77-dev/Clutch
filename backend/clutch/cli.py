"""``clutch serve`` — run the API (and the built frontend) with uvicorn."""

from __future__ import annotations

import argparse
import os

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

    load_dotenv()
    if args.db:
        os.environ["CLUTCH_DB"] = args.db
    if args.desktop:
        if args.host not in ("127.0.0.1", "localhost"):
            parser.error("--desktop only binds to 127.0.0.1: it can launch programs and delete files")
        os.environ["CLUTCH_DESKTOP"] = "1"
        if not os.environ.get("CLUTCH_TOKEN"):
            import secrets

            os.environ["CLUTCH_TOKEN"] = secrets.token_urlsafe(24)
            print(f"Desktop API token (send as X-Clutch-Token): {os.environ['CLUTCH_TOKEN']}", flush=True)
    import uvicorn

    uvicorn.run("clutch.app:create_app", factory=True, host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
