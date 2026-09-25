"""Settings from environment variables / a local ``.env`` file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # optional: plain environment variables work without it
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    player_id: str | None
    player_name: str | None
    db_path: Path
    output: Path

    @classmethod
    def load(cls, env_file: str | Path | None = ".env") -> Settings:
        if load_dotenv and env_file:
            load_dotenv(env_file)
        return cls(
            api_key=os.environ.get("BALLCHASING_API_KEY") or None,
            player_id=os.environ.get("RL_PLAYER_ID") or None,
            player_name=os.environ.get("RL_PLAYER_NAME") or None,
            db_path=Path(os.environ.get("RLSTATS_DB", "rlstats.db")),
            output=Path(os.environ.get("RLSTATS_OUTPUT", "dashboard.html")),
        )
