"""Where Clutch keeps local data, and the user's desktop settings."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any


def data_dir() -> Path:
    """``$CLUTCH_HOME``, else ``%APPDATA%/Clutch`` (``~/.clutch`` off Windows)."""
    if os.environ.get("CLUTCH_HOME"):
        base = Path(os.environ["CLUTCH_HOME"])
    elif os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"]) / "Clutch"
    else:
        base = Path.home() / ".clutch"
    base.mkdir(parents=True, exist_ok=True)
    return base


def default_clips_dir() -> Path:
    videos = Path.home() / "Videos"
    return (videos if videos.is_dir() else data_dir()) / "Clutch"


@dataclass
class Settings:
    # Clipping
    clips_dir: str = ""
    buffer_seconds: int = 60  # how far back a clip reaches
    fps: int = 60
    quality: str = "high"  # low | medium | high | ultra | max
    encoder: str = "auto"  # auto | nvenc | amf | qsv | x264 (CPU)
    codec: str = "h264"  # h264 (plays everywhere) | hevc (~half the size) | av1 (RTX 40 / RX 7000 / Arc)
    preset: str = "balanced"  # speed | balanced | quality: encoder effort
    rate_control: str = "quality"  # quality (constant quality) | bitrate (target Mbps)
    bitrate_mbps: int = 50
    resolution: str = "native"  # native | 1440 | 1080 | 720
    audio_kbps: int = 192
    audio_device: str = "default"  # "default" follows Windows' default output; or a device name
    mic_device: str = "default"
    monitor: int = 0
    record_system_audio: bool = True
    record_mic: bool = False
    auto_buffer: bool = True  # start the replay buffer whenever a known game is running
    # Hotkeys (Electron accelerator syntax)
    hotkey_clip: str = "F8"
    hotkey_record: str = "F9"
    hotkey_screenshot: str = "F10"
    # App behavior
    start_with_windows: bool = False
    minimize_to_tray: bool = True
    notify_sessions: bool = True
    auto_sync_on_exit: bool = True  # refresh linked stats when a tracked game closes
    # game id -> player key, so "my stats" and auto-sync know who you are
    linked_profiles: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.clips_dir:
            self.clips_dir = str(default_clips_dir())


CHOICES = {
    "quality": ("low", "medium", "high", "ultra", "max"),
    "encoder": ("auto", "nvenc", "amf", "qsv", "x264"),
    "codec": ("h264", "hevc", "av1"),
    "preset": ("speed", "balanced", "quality"),
    "rate_control": ("quality", "bitrate"),
    "resolution": ("native", "1440", "1080", "720"),
    "fps": (30, 60, 120, 144),
    "audio_kbps": (128, 192, 256, 320),
}


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "settings.json"
        self._lock = threading.Lock()
        self._listeners: list = []
        self.settings = self._load()

    def _load(self) -> Settings:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return Settings()
        known = {f.name for f in fields(Settings)}
        return Settings(**{k: v for k, v in raw.items() if k in known})

    def get(self) -> Settings:
        return self.settings

    def update(self, changes: dict[str, Any]) -> Settings:
        known = {f.name: f for f in fields(Settings)}
        with self._lock:
            data = asdict(self.settings)
            for key, value in changes.items():
                if key not in known:
                    raise ValueError(f"Unknown setting: {key}")
                current = data[key]
                if isinstance(current, bool) and not isinstance(value, bool):
                    raise ValueError(f"{key} must be true or false")
                if isinstance(current, int) and not isinstance(current, bool) and not isinstance(value, int):
                    raise ValueError(f"{key} must be a number")
                data[key] = value
            if data["buffer_seconds"] not in range(10, 601):
                raise ValueError("buffer_seconds must be between 10 and 600")
            for key, allowed in CHOICES.items():
                if data[key] not in allowed:
                    raise ValueError(f"{key} must be one of {', '.join(map(str, allowed))}")
            if not 5 <= data["bitrate_mbps"] <= 150:
                raise ValueError("bitrate_mbps must be between 5 and 150")
            self.settings = Settings(**data)
            self.path.write_text(json.dumps(asdict(self.settings), indent=2), encoding="utf-8")
        for fn in self._listeners:
            fn(self.settings)
        return self.settings

    def on_change(self, fn) -> None:
        self._listeners.append(fn)
