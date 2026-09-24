"""HTTP API (FastAPI). Also serves the built React app from ``frontend/dist``."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from clutch import __version__
from clutch.games import default_providers
from clutch.http import ApiError, AuthFailed, NotFound
from clutch.service import Clutch, NotConfigured, UnknownGame
from clutch.store import Store

# The built frontend: next to the repo in development, or wherever an installed build put it.
DEFAULT_DIST = (
    Path(os.environ["CLUTCH_STATIC_DIR"])
    if os.environ.get("CLUTCH_STATIC_DIR")
    else Path(__file__).resolve().parents[2] / "frontend" / "dist"
)


def create_app(service: Clutch | None = None, *, static_dir: Path | None = DEFAULT_DIST, desktop: Any = None) -> FastAPI:
    app = FastAPI(title="Clutch", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")
    svc = service or Clutch(Store(os.environ.get("CLUTCH_DB", "clutch.db")), default_providers())
    app.state.clutch = svc

    # Desktop mode (the Electron app): local library, launcher, playtime, clipping.
    if desktop is None and os.environ.get("CLUTCH_DESKTOP") == "1":
        from clutch.local.desktop import Desktop

        desktop = Desktop(svc)
    app.state.desktop = desktop
    if desktop is not None:
        from clutch.local.api import router as desktop_router

        token = os.environ.get("CLUTCH_TOKEN")
        if not token:
            raise RuntimeError("Desktop mode needs CLUTCH_TOKEN (the Electron shell sets it)")
        app.state.desktop_token = token
        app.include_router(desktop_router(desktop))
        app.router.on_shutdown.append(desktop.shutdown)

    # Vite dev server runs on another port during development.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    @app.exception_handler(UnknownGame)
    async def _unknown_game(_: Request, exc: UnknownGame) -> JSONResponse:
        return JSONResponse({"error": "UNKNOWN_GAME", "message": f"Unknown game: {exc}"}, status_code=404)

    @app.exception_handler(NotConfigured)
    async def _not_configured(_: Request, exc: NotConfigured) -> JSONResponse:
        return JSONResponse({"error": "NOT_CONFIGURED", "message": str(exc)}, status_code=503)

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound) -> JSONResponse:
        msg = str(exc) if str(exc) != "not found" else "Player not found"
        return JSONResponse({"error": "NOT_FOUND", "message": msg}, status_code=404)

    @app.exception_handler(AuthFailed)
    async def _auth(_: Request, exc: AuthFailed) -> JSONResponse:
        return JSONResponse({"error": "UPSTREAM_AUTH", "message": "The game API rejected the configured key (expired?)."}, status_code=502)

    @app.exception_handler(ApiError)
    async def _upstream(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse({"error": "UPSTREAM", "message": "The game API is unavailable right now — try again shortly."}, status_code=502)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "version": __version__, "desktop": desktop is not None, "pid": os.getpid()}

    @app.get("/api/games")
    def games() -> list[dict]:
        return svc.games()

    @app.get("/api/recent")
    def recent() -> list[dict]:
        return [p.to_dict() for p in svc.store.recent_profiles()]

    @app.get("/api/{game}/search")
    def search(game: str, q: str = Query(..., min_length=1, max_length=80)) -> dict:
        return svc.lookup(game, q).to_dict()

    def _missing(exc: LookupError) -> HTTPException:
        return HTTPException(status_code=404, detail={"error": "NOT_FOUND", "message": f"Not found: {exc}"})

    @app.get("/api/{game}/players/{key}/overview")
    def overview(game: str, key: str) -> dict:
        try:
            return svc.overview(game, key)
        except UnknownGame:
            raise
        except LookupError as exc:
            raise _missing(exc) from None

    @app.get("/api/{game}/players/{key}/matches")
    def matches(
        game: str,
        key: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        character: str | None = None,
        mode: str | None = None,
    ) -> dict:
        try:
            return svc.match_page(game, key, offset=offset, limit=limit, character=character, mode=mode)
        except UnknownGame:
            raise
        except LookupError as exc:
            raise _missing(exc) from None

    @app.get("/api/{game}/players/{key}/matches/{match_id}")
    def match(game: str, key: str, match_id: str) -> dict:
        try:
            return svc.match_detail(game, key, match_id)
        except UnknownGame:
            raise
        except LookupError as exc:
            raise _missing(exc) from None

    @app.post("/api/{game}/players/{key}/sync")
    def sync(game: str, key: str) -> dict:
        try:
            return svc.sync(game, key)
        except UnknownGame:
            raise
        except LookupError as exc:
            raise _missing(exc) from None

    if static_dir and static_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(status_code=404)
            file = static_dir / path
            if path and file.is_file() and static_dir in file.resolve().parents:
                return FileResponse(file)
            return FileResponse(static_dir / "index.html")  # client-side routes

    return app
