"""Entry point for the bundled backend (PyInstaller builds ``clutch-backend.exe`` from this)."""

from clutch.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
