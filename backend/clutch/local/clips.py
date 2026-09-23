"""The media hub: every clip, recording and screenshot Clutch has saved.

Files live in the user's clips folder (``Videos/Clutch/<Game>/...``) so they're
easy to find outside the app; this module keeps an index of them in SQLite
with titles, favourites, game, duration and a thumbnail, and does the editing
(trim, GIF, size-limited MP4 for Discord).
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from clutch.local.capture import media_info, pick_encoder, run_ffmpeg

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm"}
IMAGE_EXT = {".png", ".jpg", ".jpeg"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,            -- clip | recording | screenshot | export
    game_id     TEXT,
    game_name   TEXT,
    title       TEXT NOT NULL,
    created_at  REAL NOT NULL,
    duration    REAL,
    width       INTEGER,
    height      INTEGER,
    size        INTEGER,
    favorite    INTEGER NOT NULL DEFAULT 0,
    thumb       TEXT,
    parent_id   INTEGER                   -- the clip an export/trim was made from
);
CREATE INDEX IF NOT EXISTS clips_created ON clips(created_at);
"""

COLUMNS = (
    "id",
    "path",
    "kind",
    "game_id",
    "game_name",
    "title",
    "created_at",
    "duration",
    "width",
    "height",
    "size",
    "favorite",
    "thumb",
    "parent_id",
)


def safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip(" .") or "Desktop"


class ClipStore:
    def __init__(self, db_path: Path | str, thumbs_dir: Path) -> None:
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.thumbs = thumbs_dir
        self.thumbs.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # ── paths ───────────────────────────────────────────────────────────────
    @staticmethod
    def new_path(root: Path, game_name: str | None, kind: str, ext: str = ".mp4") -> Path:
        stamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        label = {"clip": "Clip", "recording": "Recording", "screenshot": "Screenshot"}.get(kind, kind.title())
        folder = root / safe_name(game_name or "Desktop")
        path = folder / f"{safe_name(game_name or 'Desktop')} {label} {stamp}{ext}"
        n = 2
        while path.exists():
            path = folder / f"{safe_name(game_name or 'Desktop')} {label} {stamp} ({n}){ext}"
            n += 1
        return path

    # ── index ───────────────────────────────────────────────────────────────
    def add(
        self,
        path: Path,
        *,
        kind: str,
        game_id: str | None = None,
        game_name: str | None = None,
        title: str | None = None,
        parent_id: int | None = None,
    ) -> dict[str, Any]:
        info = media_info(path) if path.suffix.lower() in VIDEO_EXT else _image_info(path)
        thumb = self._thumbnail(path, info)
        created = path.stat().st_mtime
        default_title = f"{game_name or 'Desktop'} · {datetime.fromtimestamp(created).strftime('%b %d, %I:%M %p').replace(' 0', ' ')}"
        with self._lock:
            cur = self.db.execute(
                "INSERT OR REPLACE INTO clips (path, kind, game_id, game_name, title, created_at, duration, width, height, size, thumb, parent_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(path),
                    kind,
                    game_id,
                    game_name,
                    title or default_title,
                    created,
                    info.get("duration"),
                    info.get("width"),
                    info.get("height"),
                    path.stat().st_size,
                    str(thumb) if thumb else None,
                    parent_id,
                ),
            )
            self.db.commit()
            return self.get(cur.lastrowid)

    def _thumbnail(self, path: Path, info: dict[str, Any]) -> Path | None:
        out = self.thumbs / f"{abs(hash(str(path))):x}_{int(time.time() * 1000)}.jpg"
        if path.suffix.lower() in IMAGE_EXT:
            args = ["-i", str(path), "-vf", "scale=640:-2", "-frames:v", "1", str(out)]
        else:
            at = max(0.0, (info.get("duration") or 0) * 0.6)  # later frames are more "the moment" than the first
            args = ["-ss", f"{at:.2f}", "-i", str(path), "-vf", "scale=640:-2", "-frames:v", "1", "-q:v", "4", str(out)]
        result = run_ffmpeg(args, timeout=30)
        return out if result.returncode == 0 and out.exists() else None

    def get(self, clip_id: int) -> dict[str, Any]:
        with self._lock:
            row = self.db.execute(f"SELECT {', '.join(COLUMNS)} FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if row is None:
            raise KeyError(clip_id)
        return self._row(row)

    @staticmethod
    def _row(row: tuple) -> dict[str, Any]:
        d = dict(zip(COLUMNS, row, strict=True))
        d["favorite"] = bool(d["favorite"])
        d["exists"] = Path(d["path"]).exists()
        return d

    def list(
        self, *, game_id: str | None = None, kind: str | None = None, favorites: bool = False, query: str | None = None
    ) -> list[dict[str, Any]]:
        sql = f"SELECT {', '.join(COLUMNS)} FROM clips WHERE 1=1"
        args: list[Any] = []
        if game_id:
            sql += " AND game_id = ?"
            args.append(game_id)
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        if favorites:
            sql += " AND favorite = 1"
        if query:
            sql += " AND (title LIKE ? OR game_name LIKE ?)"
            args += [f"%{query}%", f"%{query}%"]
        sql += " ORDER BY created_at DESC"
        with self._lock:
            rows = self.db.execute(sql, args).fetchall()
        return [self._row(r) for r in rows]

    def update(self, clip_id: int, *, title: str | None = None, favorite: bool | None = None) -> dict[str, Any]:
        self.get(clip_id)
        with self._lock:
            if title is not None:
                self.db.execute("UPDATE clips SET title = ? WHERE id = ?", (title.strip()[:120] or "Untitled", clip_id))
            if favorite is not None:
                self.db.execute("UPDATE clips SET favorite = ? WHERE id = ?", (int(favorite), clip_id))
            self.db.commit()
        return self.get(clip_id)

    def delete(self, clip_id: int, *, delete_file: bool = True) -> None:
        clip = self.get(clip_id)
        if delete_file and Path(clip["path"]).exists():
            try:
                from send2trash import send2trash  # Recycle Bin, so a mis-click is recoverable

                send2trash(clip["path"])
            except ImportError:
                Path(clip["path"]).unlink()
        if clip["thumb"]:
            Path(clip["thumb"]).unlink(missing_ok=True)
        with self._lock:
            self.db.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
            self.db.commit()

    def import_folder(self, root: Path) -> int:
        """Index media already in the clips folder (older clips, files copied in by hand)."""
        known = {r[0] for r in self.db.execute("SELECT path FROM clips").fetchall()}
        added = 0
        for path in sorted(root.rglob("*")):
            if str(path) in known or path.suffix.lower() not in VIDEO_EXT | IMAGE_EXT or not path.is_file():
                continue
            game = path.parent.name if path.parent != root else None
            kind = "screenshot" if path.suffix.lower() in IMAGE_EXT else ("recording" if "Recording" in path.name else "clip")
            try:
                self.add(path, kind=kind, game_name=game)
                added += 1
            except Exception:
                continue
        return added

    def prune_missing(self) -> int:
        gone = [c["id"] for c in self.list() if not c["exists"]]
        with self._lock:
            self.db.executemany("DELETE FROM clips WHERE id = ?", [(i,) for i in gone])
            self.db.commit()
        return len(gone)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            n, size, dur = self.db.execute("SELECT COUNT(*), COALESCE(SUM(size), 0), COALESCE(SUM(duration), 0) FROM clips").fetchone()
            by_kind = dict(self.db.execute("SELECT kind, COUNT(*) FROM clips GROUP BY kind").fetchall())
        return {"count": n, "bytes": size, "seconds": dur, "by_kind": by_kind}

    # ── editing ─────────────────────────────────────────────────────────────
    def trim(self, clip_id: int, start: float, end: float, *, precise: bool = True, encoder: str = "auto") -> dict[str, Any]:
        clip = self.get(clip_id)
        src = Path(clip["path"])
        if not 0 <= start < end:
            raise ValueError("Trim start must be before the end")
        dest = src.with_name(f"{src.stem} (trim {start:.0f}-{end:.0f}s){src.suffix}")
        if precise:
            # Re-encode so the cut lands on the exact frame (stream copy can only cut on keyframes).
            codec = {"nvenc": "h264_nvenc", "amf": "h264_amf", "qsv": "h264_qsv"}.get(pick_encoder(encoder), "libx264")
            quality = ["-cq", "20"] if codec == "h264_nvenc" else (["-crf", "20"] if codec == "libx264" else [])
            args = ["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src), "-c:v", codec, *quality, "-c:a", "aac", "-b:a", "192k"]
        else:
            args = ["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src), "-c", "copy"]
        result = run_ffmpeg([*args, "-movflags", "+faststart", str(dest)], timeout=600)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip()[-300:] or "Trim failed")
        return self.add(
            dest,
            kind=clip["kind"],
            game_id=clip["game_id"],
            game_name=clip["game_name"],
            title=f"{clip['title']} (trimmed)",
            parent_id=clip_id,
        )

    def export_gif(self, clip_id: int, start: float = 0, end: float | None = None, *, width: int = 480, fps: int = 15) -> dict[str, Any]:
        clip = self.get(clip_id)
        src = Path(clip["path"])
        end = min(end if end is not None else (clip["duration"] or 10), start + 30)  # GIFs over 30 s are unusable
        dest = src.with_name(f"{src.stem} {start:.0f}-{end:.0f}s.gif")
        # Two-pass palette: a per-clip palette looks far better than GIF's default 256 colours.
        chain = f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4"
        result = run_ffmpeg(
            ["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src), "-filter_complex", chain, "-loop", "0", str(dest)], timeout=600
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip()[-300:] or "GIF export failed")
        return self.add(
            dest, kind="export", game_id=clip["game_id"], game_name=clip["game_name"], title=f"{clip['title']} (GIF)", parent_id=clip_id
        )

    def export_compact(self, clip_id: int, target_mb: float = 9.5, start: float = 0, end: float | None = None) -> dict[str, Any]:
        """An MP4 that fits a size cap (Discord's free upload limit is 10 MB)."""
        clip = self.get(clip_id)
        src = Path(clip["path"])
        end = end if end is not None else (clip["duration"] or 30)
        length = max(1.0, end - start)
        audio_kbps = 96
        video_kbps = max(150, int(target_mb * 8192 / length) - audio_kbps)
        height = 1080 if video_kbps > 6000 else 720 if video_kbps > 2000 else 480
        dest = src.with_name(f"{src.stem} ({target_mb:.0f}MB){src.suffix}")
        result = run_ffmpeg(
            [
                "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
                "-vf", f"scale=-2:'min({height},ih)'",
                "-c:v", "libx264", "-preset", "medium", "-b:v", f"{video_kbps}k", "-maxrate", f"{int(video_kbps * 1.2)}k", "-bufsize", f"{video_kbps * 2}k",
                "-c:a", "aac", "-b:a", f"{audio_kbps}k", "-movflags", "+faststart", str(dest),
            ],
            timeout=900,
        )  # fmt: skip
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip()[-300:] or "Export failed")
        return self.add(
            dest,
            kind="export",
            game_id=clip["game_id"],
            game_name=clip["game_name"],
            title=f"{clip['title']} ({target_mb:.0f} MB)",
            parent_id=clip_id,
        )


def _image_info(path: Path) -> dict[str, Any]:
    try:
        from PIL import Image

        with Image.open(path) as im:
            return {"duration": None, "width": im.width, "height": im.height}
    except Exception:
        return {"duration": None, "width": None, "height": None}
