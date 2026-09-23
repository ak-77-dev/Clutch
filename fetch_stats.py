"""Backwards-compatible entry point — same as `rlstats sync --no-dashboard`."""

from rlstats.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["sync", "--no-dashboard"]))
