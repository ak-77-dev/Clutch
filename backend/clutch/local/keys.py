"""API keys for the stats providers, editable from the app.

Keys live in a ``.env`` file next to the database (the backend's working
directory: the repo's ``backend/`` in development, ``%APPDATA%/Clutch`` in an
installed build). Writes keep every other line and comment of the file intact.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

# (env var, game, label, where to get one)
KEYS: list[dict[str, str]] = [
    {"name": "RIOT_API_KEY", "game": "lol", "label": "Riot API key", "help": "https://developer.riotgames.com"},
    {"name": "HENRIK_API_KEY", "game": "valorant", "label": "HenrikDev API key", "help": "https://docs.henrikdev.xyz"},
    {"name": "BALLCHASING_API_KEY", "game": "rocketleague", "label": "ballchasing.com token", "help": "https://ballchasing.com/upload"},
    # Experimental: the ACT_SSO_COOKIE value from a signed-in callofduty.com session.
    {"name": "COD_SSO_TOKEN", "game": "cod", "label": "Activision SSO token", "help": "https://www.callofduty.com"},
]
PLAIN = {"LOL_PLATFORM"}  # not secret: shown as-is
NAMES = {k["name"] for k in KEYS} | PLAIN
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def env_path() -> Path:
    return Path(os.environ.get("CLUTCH_ENV_FILE") or Path.cwd() / ".env")


def mask(value: str) -> str:
    return "" if not value else ("•" * 4 + value[-4:] if len(value) > 8 else "•" * len(value))


def status() -> dict[str, Any]:
    return {
        "path": str(env_path()),
        "keys": [{**k, "set": bool(os.environ.get(k["name"])), "preview": mask(os.environ.get(k["name"], ""))} for k in KEYS],
        "lol_platform": os.environ.get("LOL_PLATFORM", "na1"),
    }


def write_env(path: Path, updates: dict[str, str | None]) -> None:
    """Set (or, with ``None``/``""``, remove) variables in a dotenv file, keeping everything else."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(updates)
    out: list[str] = []
    for line in lines:
        m = _LINE.match(line)
        if m and m.group(1) in pending:
            value = pending.pop(m.group(1))
            if value:
                out.append(f"{m.group(1)}={value}")
            continue
        out.append(line)
    out += [f"{k}={v}" for k, v in pending.items() if v]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def update(values: dict[str, str | None]) -> dict[str, Any]:
    unknown = set(values) - NAMES
    if unknown:
        raise ValueError(f"Unknown key: {', '.join(sorted(unknown))}")
    clean: dict[str, str | None] = {}
    for name, value in values.items():
        value = (value or "").strip()
        if any(c in value for c in "\r\n\"' "):
            raise ValueError(f"{name} can't contain spaces, quotes or line breaks")
        clean[name] = value or None
    write_env(env_path(), clean)
    for name, value in clean.items():
        if value:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)
    return status()
