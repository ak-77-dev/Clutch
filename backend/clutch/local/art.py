"""Cover art and icons for library games.

- Steam games use their own app id. Anything else is matched by name against
  Steam's public store search (many Battle.net, EA and Ubisoft games are also
  sold on Steam), which gives portrait covers and hero banners from Valve's CDN.
- Every game also gets its real icon, extracted from its executable (or the
  launcher's ``.ico``), plus that icon's dominant colour for designed fallback tiles.
"""

from __future__ import annotations

import colorsys
import io
import json
import re
import threading
from pathlib import Path
from typing import Any

import requests

from clutch.local.library import Game

STEAM_CDN = "https://cdn.cloudflare.steamstatic.com/steam/apps"
STORE_SEARCH = "https://store.steampowered.com/api/storesearch/"
# Edition words launchers append that Steam listings don't have.
NAME_SUFFIXES = re.compile(r"\s+(pc|enhanced|enhanced edition|windows 10|for windows|game)$", re.I)


def normalize(name: str) -> str:
    name = re.sub("[™®©]", "", name).lower()
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def art_urls(appid: int | None) -> dict[str, str | None]:
    if not appid:
        return {"cover": None, "hero": None, "header": None, "logo": None}
    base = f"{STEAM_CDN}/{appid}"
    return {
        "cover": f"{base}/library_600x900_2x.jpg",
        "hero": f"{base}/library_hero.jpg",
        "header": f"{base}/header.jpg",
        "logo": f"{base}/logo.png",
    }


class ArtResolver:
    def __init__(self, cache_dir: Path, session: requests.Session | None = None) -> None:
        self.cache_dir = cache_dir
        self.icons = cache_dir / "icons"
        self.icons.mkdir(parents=True, exist_ok=True)
        self.index_path = cache_dir / "art.json"
        self.session = session or requests.Session()
        self._lock = threading.Lock()
        try:
            self.index: dict[str, Any] = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.index = {}

    def _save(self) -> None:
        self.index_path.write_text(json.dumps(self.index, indent=1), encoding="utf-8")

    # ── Steam match ─────────────────────────────────────────────────────────
    def steam_appid(self, game: Game) -> int | None:
        if game.steam_appid:
            return game.steam_appid
        key = f"appid:{normalize(game.name)}"
        if key in self.index:
            return self.index[key]
        appid = self._search(game.name)
        with self._lock:
            self.index[key] = appid
            self._save()
        return appid

    def _search(self, name: str) -> int | None:
        wanted = {normalize(name), normalize(NAME_SUFFIXES.sub("", name))}
        try:
            resp = self.session.get(STORE_SEARCH, params={"term": NAME_SUFFIXES.sub("", name), "l": "english", "cc": "US"}, timeout=8)
            items = resp.json().get("items", []) if resp.ok else []
        except (requests.RequestException, ValueError):
            return None
        # Only an exact (normalized) title match: "Modern Warfare" must not become "Modern Warfare III".
        return next((int(i["id"]) for i in items if normalize(i.get("name", "")) in wanted), None)

    # ── icons ───────────────────────────────────────────────────────────────
    def icon_png(self, game: Game) -> Path | None:
        """The game's real icon as a cached 256px PNG (None if it can't be read)."""
        out = self.icons / f"{re.sub(r'[^a-zA-Z0-9_.-]', '_', game.id)}.png"
        if out.exists():
            return out
        src = game.icon_path or game.exe
        if not src or not Path(src).exists():
            return None
        try:
            from PIL import Image

            if src.lower().endswith(".ico"):
                img = Image.open(src)
                img.size = max(img.info.get("sizes") or [img.size])
            elif src.lower().endswith((".png", ".jpg", ".jpeg")):
                img = Image.open(src)
            else:
                from icoextract import IconExtractor

                img = Image.open(io.BytesIO(IconExtractor(src).get_icon(num=0).getvalue()))
                img.size = max(img.info.get("sizes") or [img.size])
            img.load()
            img = img.convert("RGBA")
            if max(img.size) < 256:  # small launcher icons: upscale smoothly so tiles stay crisp
                img = img.resize((256, 256), Image.LANCZOS)
            img.save(out, "PNG")
            return out
        except Exception:  # not every exe carries an icon resource
            return None

    def accent(self, game: Game) -> str | None:
        """A vivid colour from the icon, for tinting tiles that have no cover art."""
        key = f"accent:{game.id}"
        if key in self.index:
            return self.index[key]
        icon = self.icon_png(game)
        color = dominant_color(icon) if icon else None
        with self._lock:
            self.index[key] = color
            self._save()
        return color

    def describe(self, game: Game, resolve: bool = True) -> dict[str, Any]:
        """Art URLs and accent colour. ``resolve=False`` only uses what's cached (no network, no disk work)."""
        if resolve:
            appid, accent = self.steam_appid(game), self.accent(game)
        else:
            appid = game.steam_appid or self.index.get(f"appid:{normalize(game.name)}")
            accent = self.index.get(f"accent:{game.id}")
        return {**art_urls(appid), "steam_appid": appid, "accent": accent}


def dominant_color(path: Path) -> str | None:
    """Most common saturated colour in an image, as ``#rrggbb``."""
    from PIL import Image

    try:
        img = Image.open(path).convert("RGBA").resize((48, 48))
    except OSError:
        return None
    buckets: dict[tuple[int, int, int], float] = {}
    for r, g, b, a in img.getdata():
        if a < 128:
            continue
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s < 0.25 or v < 0.2:
            continue  # greys and near-black say nothing about the game
        key = (r // 32, g // 32, b // 32)
        buckets[key] = buckets.get(key, 0) + s * v
    if not buckets:
        return None
    r, g, b = max(buckets, key=buckets.get)
    return f"#{r * 32 + 16:02x}{g * 32 + 16:02x}{b * 32 + 16:02x}"
