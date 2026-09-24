"""``/api/desktop/*``: library, launcher, playtime, capture and the media hub.

These routes can launch programs and delete files, so they only exist when the
server runs in desktop mode (bound to 127.0.0.1), and every request must carry
the per-launch token the Electron shell generated (``X-Clutch-Token`` header,
or ``?token=`` for ``<video>``/``<img>`` URLs, which can't send headers). That
stops any web page the player visits from driving their local Clutch.
"""

from __future__ import annotations

import asyncio
import hmac
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from clutch.local import library as lib
from clutch.local.desktop import Desktop, EventBus


def require_token(request: Request) -> None:
    expected = request.app.state.desktop_token
    given = request.headers.get("x-clutch-token") or request.query_params.get("token") or ""
    if not hmac.compare_digest(given, expected):
        raise HTTPException(status_code=401, detail={"error": "UNAUTHORIZED", "message": "Missing or wrong desktop token"})


class SettingsPatch(BaseModel):
    model_config = {"extra": "allow"}


class Hidden(BaseModel):
    hidden: bool


class CustomGame(BaseModel):
    name: str
    exe: str


class ClipPatch(BaseModel):
    title: str | None = None
    favorite: bool | None = None


class Range(BaseModel):
    start: float = 0
    end: float | None = None
    precise: bool = True


class Compact(BaseModel):
    target_mb: float = 9.5
    start: float = 0
    end: float | None = None


class BufferToggle(BaseModel):
    on: bool


class SaveClip(BaseModel):
    seconds: float | None = None


class Montage(BaseModel):
    clip_ids: list[int]
    title: str | None = None


class Caption(BaseModel):
    text: str
    position: str = "bottom"


class Vertical(BaseModel):
    mode: str = "blur"


class GoalIn(BaseModel):
    kind: str
    target: float
    game_id: str | None = None
    label: str | None = None


class FriendIn(BaseModel):
    game: str
    query: str


def _bad(exc: Exception, code: int = 400) -> HTTPException:
    return HTTPException(status_code=code, detail={"error": "DESKTOP", "message": str(exc)})


def router(desktop: Desktop) -> APIRouter:
    r = APIRouter(prefix="/api/desktop", dependencies=[Depends(require_token)])

    # ── status & events ─────────────────────────────────────────────────────
    @r.get("/status")
    def status() -> dict[str, Any]:
        return desktop.status()

    @r.get("/events")
    async def events(request: Request) -> StreamingResponse:
        q = desktop.events.subscribe()

        async def stream():
            try:
                yield "retry: 2000\n\n"
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.to_thread(q.get, True, 15)
                        yield EventBus.sse(event)
                    except Exception:
                        yield ": keep-alive\n\n"
            finally:
                desktop.events.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @r.post("/shutdown")
    def shutdown() -> dict[str, Any]:
        """Called by the Electron shell on quit: stop FFmpeg and close open sessions, then exit."""
        import threading

        desktop.shutdown()
        threading.Timer(0.3, os._exit, args=(0,)).start()
        return {"ok": True}

    @r.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        return desktop.capabilities()

    # ── settings ────────────────────────────────────────────────────────────
    @r.get("/settings")
    def get_settings() -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(desktop.cfg)

    @r.put("/settings")
    def put_settings(patch: SettingsPatch) -> dict[str, Any]:
        from dataclasses import asdict

        try:
            return asdict(desktop.settings.update(patch.model_dump()))
        except ValueError as exc:
            raise _bad(exc) from None

    # ── library / launcher ──────────────────────────────────────────────────
    @r.get("/library")
    def library(hidden: bool = False) -> list[dict[str, Any]]:
        return desktop.library_view(include_hidden=hidden)

    @r.post("/library/scan")
    def scan() -> list[dict[str, Any]]:
        desktop.library.scan()
        import threading

        threading.Thread(target=desktop._warm_art, daemon=True).start()
        return desktop.library_view()

    def _game(game_id: str) -> lib.Game:
        try:
            return desktop.library.get(game_id)
        except KeyError:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND", "message": "No such game in your library"}) from None

    @r.post("/library/{game_id}/launch")
    def launch(game_id: str) -> dict[str, Any]:
        game = _game(game_id)
        try:
            lib.launch(game)
        except Exception as exc:
            raise _bad(exc, 500) from None
        desktop.events.publish("launching", game_id=game.id, game_name=game.name)
        return {"ok": True}

    @r.post("/library/{game_id}/hidden")
    def hide(game_id: str, body: Hidden) -> dict[str, Any]:
        _game(game_id)
        desktop.library.set_hidden(game_id, body.hidden)
        return {"ok": True}

    @r.post("/library/custom")
    def add_custom(body: CustomGame) -> dict[str, Any]:
        if not Path(body.exe).is_file():
            raise _bad(ValueError("That file doesn't exist"))
        try:
            return desktop.library.add_custom(body.name.strip() or Path(body.exe).stem, body.exe).to_dict()
        except ValueError as exc:
            raise _bad(exc) from None

    @r.delete("/library/custom/{game_id}")
    def remove_custom(game_id: str) -> dict[str, Any]:
        desktop.library.remove_custom(game_id)
        return {"ok": True}

    @r.get("/library/{game_id}/icon")
    def icon(game_id: str) -> FileResponse:
        path = desktop.art.icon_png(_game(game_id))
        if not path:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND", "message": "No icon"})
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "max-age=86400"})

    # ── playtime ────────────────────────────────────────────────────────────
    @r.get("/playtime")
    def playtime(days: int = Query(182, ge=7, le=730)) -> dict[str, Any]:
        return desktop.playtime_report(days)

    @r.get("/playtime/{game_id}/sessions")
    def game_sessions(game_id: str) -> list[dict[str, Any]]:
        return desktop.playtime.sessions(game_id, limit=200)

    # ── capture ─────────────────────────────────────────────────────────────
    @r.post("/capture/buffer")
    def buffer(body: BufferToggle) -> dict[str, Any]:
        try:
            return desktop.set_buffer(body.on)
        except Exception as exc:
            raise _bad(exc, 500) from None

    @r.post("/capture/clip")
    def clip(body: SaveClip | None = None) -> dict[str, Any]:
        try:
            return desktop.save_clip(body.seconds if body else None)
        except RuntimeError as exc:
            desktop.events.publish("error", message=str(exc))
            raise _bad(exc, 409) from None

    @r.post("/capture/record")
    def record() -> dict[str, Any]:
        try:
            return desktop.toggle_recording()
        except RuntimeError as exc:
            raise _bad(exc, 409) from None

    @r.post("/capture/screenshot")
    def shot() -> dict[str, Any]:
        try:
            return desktop.take_screenshot()
        except RuntimeError as exc:
            raise _bad(exc, 500) from None

    # ── media hub ───────────────────────────────────────────────────────────
    @r.get("/clips")
    def clips(game_id: str | None = None, kind: str | None = None, favorites: bool = False, q: str | None = None) -> dict[str, Any]:
        return {"items": desktop.clips.list(game_id=game_id, kind=kind, favorites=favorites, query=q), "stats": desktop.clips.stats()}

    def _clip(clip_id: int) -> dict[str, Any]:
        try:
            return desktop.clips.get(clip_id)
        except KeyError:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND", "message": "No such clip"}) from None

    @r.get("/clips/{clip_id}")
    def clip_detail(clip_id: int) -> dict[str, Any]:
        return _clip(clip_id)

    @r.get("/clips/{clip_id}/file")
    def clip_file(clip_id: int) -> FileResponse:
        c = _clip(clip_id)
        if not c["exists"]:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND", "message": "The file was moved or deleted"})
        return FileResponse(c["path"])  # honours Range requests, so the player can seek

    @r.get("/clips/{clip_id}/thumb")
    def clip_thumb(clip_id: int) -> FileResponse:
        c = _clip(clip_id)
        if not c["thumb"] or not Path(c["thumb"]).exists():
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND", "message": "No thumbnail"})
        return FileResponse(c["thumb"], media_type="image/jpeg", headers={"Cache-Control": "max-age=604800"})

    @r.patch("/clips/{clip_id}")
    def patch_clip(clip_id: int, body: ClipPatch) -> dict[str, Any]:
        _clip(clip_id)
        return desktop.clips.update(clip_id, title=body.title, favorite=body.favorite)

    @r.delete("/clips/{clip_id}")
    def delete_clip(clip_id: int) -> dict[str, Any]:
        _clip(clip_id)
        desktop.clips.delete(clip_id)
        return {"ok": True}

    @r.post("/clips/{clip_id}/trim")
    def trim(clip_id: int, body: Range) -> dict[str, Any]:
        c = _clip(clip_id)
        try:
            return desktop.clips.trim(
                clip_id, body.start, body.end or c["duration"] or 0, precise=body.precise, encoder=desktop.cfg.encoder
            )
        except (ValueError, RuntimeError) as exc:
            raise _bad(exc) from None

    @r.post("/clips/{clip_id}/gif")
    def gif(clip_id: int, body: Range) -> dict[str, Any]:
        _clip(clip_id)
        try:
            return desktop.clips.export_gif(clip_id, body.start, body.end)
        except RuntimeError as exc:
            raise _bad(exc) from None

    @r.post("/clips/{clip_id}/compact")
    def compact(clip_id: int, body: Compact) -> dict[str, Any]:
        _clip(clip_id)
        try:
            return desktop.clips.export_compact(clip_id, body.target_mb, body.start, body.end)
        except RuntimeError as exc:
            raise _bad(exc) from None

    @r.post("/clips/{clip_id}/reveal")
    def reveal(clip_id: int) -> dict[str, Any]:
        c = _clip(clip_id)
        if sys.platform == "win32":  # pragma: no cover - opens Explorer
            subprocess.Popen(["explorer", "/select,", os.path.normpath(c["path"])])
        return {"ok": True}

    # ── editing & sharing ───────────────────────────────────────────────────
    def _edit(fn, *args):
        try:
            return fn(*args)
        except KeyError:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND", "message": "No such clip"}) from None
        except (ValueError, RuntimeError) as exc:
            raise _bad(exc) from None

    @r.post("/clips/montage")
    def montage(body: Montage) -> dict[str, Any]:
        return _edit(desktop.make_montage, body.clip_ids, body.title)

    @r.post("/clips/{clip_id}/caption")
    def caption(clip_id: int, body: Caption) -> dict[str, Any]:
        return _edit(desktop.make_caption, clip_id, body.text, body.position)

    @r.post("/clips/{clip_id}/vertical")
    def vertical(clip_id: int, body: Vertical) -> dict[str, Any]:
        return _edit(desktop.make_vertical, clip_id, body.mode)

    @r.post("/clips/{clip_id}/without-mic")
    def without_mic(clip_id: int) -> dict[str, Any]:
        return _edit(desktop.make_without_mic, clip_id)

    @r.post("/clips/{clip_id}/share")
    def share_clip(clip_id: int) -> dict[str, Any]:
        from clutch.local.share import ShareError

        try:
            return desktop.share_clip(clip_id)
        except KeyError:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND", "message": "No such clip"}) from None
        except ShareError as exc:
            raise _bad(exc, 502) from None

    @r.get("/clips/{clip_id}/match")
    def clip_match(clip_id: int) -> dict[str, Any]:
        _clip(clip_id)
        return {"match": desktop.clip_match(clip_id)}

    @r.get("/matches/{game}/{key}/clips")
    def match_clips(game: str, key: str) -> dict[str, list[int]]:
        return desktop.match_clips(game, key)

    # ── storage ─────────────────────────────────────────────────────────────
    @r.get("/storage")
    def storage(max_days: int | None = None, max_gb: float | None = None) -> dict[str, Any]:
        return {**desktop.clips.stats(), "plan": desktop.storage_plan(max_days, max_gb)}

    @r.post("/storage/clean")
    def storage_clean() -> dict[str, Any]:
        return desktop.enforce_storage() or {"count": 0, "bytes": 0}

    # ── reports, goals, friends, session ────────────────────────────────────
    @r.get("/reports")
    def reports(limit: int = Query(20, ge=1, le=200)) -> list[dict[str, Any]]:
        return desktop.reports.recent(limit)

    @r.get("/reports/{report_id}")
    def report(report_id: int) -> dict[str, Any]:
        try:
            return desktop.reports.get(report_id)
        except KeyError:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND", "message": "No such report"}) from None

    @r.get("/goals")
    def goals() -> list[dict[str, Any]]:
        return desktop.goals_view()

    @r.post("/goals")
    def add_goal(body: GoalIn) -> dict[str, Any]:
        try:
            return desktop.goals.add(body.kind, body.target, body.game_id, body.label)
        except ValueError as exc:
            raise _bad(exc) from None

    @r.delete("/goals/{goal_id}")
    def delete_goal(goal_id: int) -> dict[str, Any]:
        desktop.goals.delete(goal_id)
        return {"ok": True}

    @r.get("/friends")
    def friends() -> dict[str, Any]:
        return desktop.friends_view()

    @r.post("/friends")
    def add_friend(body: FriendIn) -> dict[str, Any]:
        try:
            return desktop.add_friend(body.game, body.query)
        except Exception as exc:
            raise _bad(exc, 404) from None

    @r.delete("/friends/{game}/{key}")
    def remove_friend(game: str, key: str) -> dict[str, Any]:
        desktop.friends.remove(game, key)
        return {"ok": True}

    @r.post("/friends/refresh")
    def refresh_friends() -> dict[str, Any]:
        return {"refreshed": desktop.refresh_friends(force=True)}

    @r.get("/session")
    def session() -> dict[str, Any]:
        return desktop.session_view()

    # ── auto-clip ───────────────────────────────────────────────────────────
    @r.get("/autoclip")
    def autoclip() -> list[dict[str, Any]]:
        return desktop.autoclip_games()

    @r.post("/autoclip/{game_id}/install")
    def install_autoclip(game_id: str) -> dict[str, Any]:
        try:
            return {"path": desktop.install_autoclip(game_id)}
        except (KeyError, ValueError) as exc:
            raise _bad(exc) from None

    @r.post("/clips/import")
    def import_clips() -> dict[str, Any]:
        removed = desktop.clips.prune_missing()
        return {"added": desktop.clips.import_folder(desktop.clips_root), "removed": removed}

    return r
