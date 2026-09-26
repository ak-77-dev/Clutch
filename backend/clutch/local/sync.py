"""Carry API keys and linked accounts between PCs through a folder the player syncs.

Clutch has no server, so keys (``.env``) and settings live on each PC. Pointing
Settings → Sync at a OneDrive / Dropbox / Google Drive folder makes Clutch keep
``clutch-sync.json`` there: the stats API keys, linked accounts (with their
profile record, so a PC that has never looked the player up can sync them), and
the SteamGridDB / Discord ids. Every PC using that folder reads changes from it
and writes its own changes back. The local ``.env`` and ``settings.json`` are
still written, so a PC keeps working while the folder is unavailable.

The file holds API keys in plain text, like the ``.env`` does: it's only as
private as the cloud folder it sits in.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from clutch.local import keys

FILE_NAME = "clutch-sync.json"
SETTINGS = ("linked_profiles", "steamgriddb_key", "discord_client_id")


def suggested_folder() -> str | None:
    """A ``Clutch`` folder in the player's OneDrive, when OneDrive is set up."""
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        root = os.environ.get(var)
        if root and Path(root).is_dir():
            return str(Path(root) / "Clutch")
    return None


def local_keys() -> dict[str, str]:
    return {n: os.environ[n] for n in sorted(keys.NAMES) if os.environ.get(n)}


class SyncFolder:
    """Reads and writes ``clutch-sync.json`` for one :class:`~clutch.local.desktop.Desktop`."""

    def __init__(self, desktop: Any) -> None:
        self.desktop = desktop
        self._lock = threading.RLock()
        self._seen: float | None = None  # mtime of the version last read or written
        self._applying = False  # pulling: don't echo the changes back as a push
        self.last_synced: float | None = None
        self.error: str | None = None

    # ── where ───────────────────────────────────────────────────────────────
    @property
    def folder(self) -> Path | None:
        f = self.desktop.cfg.sync_folder
        return Path(f) if f else None

    @property
    def file(self) -> Path | None:
        return self.folder / FILE_NAME if self.folder else None

    def status(self) -> dict[str, Any]:
        return {
            "folder": str(self.folder) if self.folder else "",
            "file": str(self.file) if self.file else "",
            "last_synced": self.last_synced,
            "error": self.error,
            "suggested": suggested_folder(),
        }

    # ── the file ────────────────────────────────────────────────────────────
    def _read(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))  # type: ignore[union-attr]
        except FileNotFoundError:
            return None
        return data if isinstance(data, dict) else None

    def _snapshot(self) -> dict[str, Any]:
        cfg = self.desktop.cfg
        profiles: dict[str, Any] = {}
        store = getattr(self.desktop.stats, "store", None)
        for game, key in cfg.linked_profiles.items():
            with contextlib.suppress(Exception):
                if store and (p := store.profile(game, key)):
                    profiles[game] = p.to_dict()
        return {
            "version": 1,
            "updated_at": time.time(),
            "keys": local_keys(),
            "settings": {k: getattr(cfg, k) for k in SETTINGS},
            "profiles": profiles,
        }

    def _write(self, data: dict[str, Any]) -> None:
        path = self.file
        assert path is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)  # a half-written file never reaches the other PCs
        self._seen = path.stat().st_mtime
        self.last_synced = time.time()

    # ── pull / push ─────────────────────────────────────────────────────────
    def enable(self) -> None:
        """First sync after choosing a folder: merge this PC into what's there, keys the
        folder doesn't have yet are added from this PC, so no PC wipes another's keys."""
        with self._lock:
            self._seen = None
            if not self.file:
                self.error = None
                return
            try:
                existing = self._read()
                if existing:
                    merged_keys = {**local_keys(), **(existing.get("keys") or {})}
                    settings = dict(existing.get("settings") or {})
                    mine = self.desktop.cfg
                    settings["linked_profiles"] = {**mine.linked_profiles, **(settings.get("linked_profiles") or {})}
                    for k in ("steamgriddb_key", "discord_client_id"):
                        settings[k] = settings.get(k) or getattr(mine, k)
                    self._apply({**existing, "keys": merged_keys, "settings": settings})
                self._write(self._snapshot())
                self.error = None
            except (OSError, ValueError) as exc:
                self.error = f"Couldn't use the sync folder: {exc}"

    def pull(self) -> bool:
        """Apply the folder's version if another PC changed it. Cheap when nothing did."""
        with self._lock:
            if not self.file:
                return False
            try:
                mtime = self.file.stat().st_mtime
            except OSError:
                return False  # folder offline / not synced down yet: keep the local copy
            if mtime == self._seen:
                return False
            try:
                data = self._read()
            except (OSError, ValueError) as exc:
                self.error = f"Couldn't read {FILE_NAME}: {exc}"
                return False
            self._seen = mtime
            if data:
                self._apply(data)
            self.last_synced = time.time()
            self.error = None
            return True

    def push(self) -> None:
        with self._lock:
            if not self.file or self._applying:
                return
            try:
                self._write(self._snapshot())
                self.error = None
            except OSError as exc:
                self.error = f"Couldn't write to the sync folder: {exc}"

    def _apply(self, data: dict[str, Any]) -> None:
        self._applying = True
        try:
            self._import_profiles(data.get("profiles") or {})
            wanted = {k: v for k, v in (data.get("keys") or {}).items() if k in keys.NAMES and isinstance(v, str)}
            current = local_keys()
            changes: dict[str, Any] = {n: wanted.get(n) for n in keys.NAMES if wanted.get(n) != current.get(n)}
            if changes:
                self.desktop.set_keys(changes)
            cfg = self.desktop.cfg
            patch = {k: v for k, v in (data.get("settings") or {}).items() if k in SETTINGS and v != getattr(cfg, k)}
            if patch:
                self.desktop.settings.update(patch)
        finally:
            self._applying = False

    def _import_profiles(self, profiles: dict[str, Any]) -> None:
        """Linked accounts point at a stats profile; add the ones this PC has never seen."""
        stats = self.desktop.stats
        if stats is None:
            return
        from clutch.models import Profile

        for game, data in profiles.items():
            with contextlib.suppress(Exception):
                if stats.store.profile(game, data["key"]):
                    continue
                if data.get("demo"):
                    stats._ensure_demo(stats.provider(game))
                else:
                    stats.store.save_profile(Profile(**data))
