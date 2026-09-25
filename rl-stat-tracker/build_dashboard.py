"""Backwards-compatible entry point — same as `rlstats dashboard`."""

from rlstats.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["dashboard"]))
