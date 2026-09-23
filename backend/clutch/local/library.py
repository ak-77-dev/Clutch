"""Find installed games across every launcher, and launch them.

Sources, each read from the launcher's own on-disk state (nothing is guessed):

- Steam:  ``steamapps/libraryfolders.vdf`` + ``appmanifest_*.acf`` in every library
- Epic:   ``ProgramData/Epic/EpicGamesLauncher/Data/Manifests/*.item``
- Riot:   ``ProgramData/Riot Games/Metadata/<product>.live/*.product_settings.yaml``
- Xbox:   ``<drive>:/XboxGames/*/Content/MicrosoftGame.config``
- Battle.net, Ubisoft Connect, EA app, Rockstar: the Windows uninstall registry,
  recognised by each launcher's uninstaller signature

Every game gets a stable id (``steam:230410``, ``bnet:odin``, ...), an install
directory (used by the playtime monitor to spot it running) and a launch recipe.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROGRAM_DATA = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData"))
STEAM_DEFAULT = Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Steam"

# Steam tools that show up as "apps" but aren't games.
STEAM_SKIP = re.compile(r"redistributable|steamworks|proton|steam linux runtime|dedicated server|\bsdk\b", re.I)

RIOT_PRODUCTS = {
    "valorant": "VALORANT",
    "league_of_legends": "League of Legends",
    "bacon": "Legends of Runeterra",
    "lion": "2XKO",
}

# Battle.net uninstall uid -> product code accepted by ``Battle.net.exe --exec="launch <code>"``.
BNET_CODES = {
    "odin": "ODIN",  # Call of Duty: Modern Warfare (2019) / Warzone
    "auks": "AUKS",  # Call of Duty (MWII / MWIII / Black Ops 6 / Black Ops 7)
    "zeus": "ZEUS",  # Black Ops Cold War
    "fore": "FORE",  # Vanguard
    "viper": "VIPR",  # Black Ops 4
    "lazarus": "LAZR",  # Modern Warfare 2 Campaign Remastered
    "prometheus": "Pro",  # Overwatch
    "fenris": "Fen",  # Diablo IV
    "wow": "WoW",
    "wow_classic": "WoWC",
    "hs_beta": "WTCG",  # Hearthstone
    "hero": "Hero",  # Heroes of the Storm
    "s2": "S2",
    "s1": "S1",
    "d3": "D3",
    "w3": "W3",
    "anbs": "ANBS",  # Diablo Immortal
    "rtro": "RTRO",  # Blizzard Arcade Collection
    "gryphon": "GRY",  # Warcraft Rumble
}


@dataclass
class Game:
    id: str
    name: str
    source: str  # steam | epic | riot | battlenet | ubisoft | ea | rockstar | xbox | custom
    install_dir: str
    launch: dict[str, Any]  # {"uri": ...} | {"exe": ..., "args": [...]} | {"appx": identity, "app_id": ...}
    exe: str | None = None
    steam_appid: int | None = None  # for cover art (also looked up by name for non-Steam games)
    last_played: int | None = None  # unix seconds, when the launcher records it
    icon_path: str | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── parsers ──────────────────────────────────────────────────────────────────


def parse_vdf(text: str) -> dict[str, Any]:
    """Valve KeyValues (the ``.vdf`` / ``.acf`` text format) -> nested dict."""
    tokens = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text)
    root: dict[str, Any] = {}
    stack = [root]
    key: str | None = None
    for quoted, brace in tokens:
        if brace == "{":
            child: dict[str, Any] = {}
            stack[-1][key or ""] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
        elif key is None:
            key = quoted.replace("\\\\", "\\")
        else:
            stack[-1][key] = quoted.replace("\\\\", "\\")
            key = None
    return root


def scan_steam(steam_dir: Path = STEAM_DEFAULT) -> list[Game]:
    lib_file = steam_dir / "steamapps" / "libraryfolders.vdf"
    try:
        libs = parse_vdf(lib_file.read_text(encoding="utf-8", errors="replace")).get("libraryfolders", {})
    except OSError:
        return []
    cache = steam_dir / "appcache" / "librarycache"
    games = []
    for lib in libs.values():
        if not isinstance(lib, dict) or "path" not in lib:
            continue
        apps = Path(lib["path"]) / "steamapps"
        for manifest in sorted(apps.glob("appmanifest_*.acf")):
            try:
                app = parse_vdf(manifest.read_text(encoding="utf-8", errors="replace")).get("AppState", {})
            except OSError:
                continue
            name, appid = app.get("name", ""), app.get("appid", "")
            if not appid.isdigit() or STEAM_SKIP.search(name):
                continue
            games.append(
                Game(
                    id=f"steam:{appid}",
                    name=name,
                    source="steam",
                    install_dir=str(apps / "common" / app.get("installdir", name)),
                    launch={"uri": f"steam://rungameid/{appid}"},
                    steam_appid=int(appid),
                    last_played=int(app["LastPlayed"]) if app.get("LastPlayed", "0").isdigit() and app.get("LastPlayed") != "0" else None,
                    icon_path=_steam_icon(cache, appid),
                )
            )
    return games


def scan_epic(manifests: Path = PROGRAM_DATA / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests") -> list[Game]:
    games = []
    for item in sorted(manifests.glob("*.item")):
        try:
            d = json.loads(item.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        app = d.get("AppName", "")
        if not d.get("bIsApplication", True) or (d.get("MainGameAppName") and d["MainGameAppName"] != app):
            continue  # DLC and non-game content
        ids = ":".join((d.get("CatalogNamespace", ""), d.get("CatalogItemId", ""), app))
        install = d.get("InstallLocation", "")
        exe = str(Path(install) / d["LaunchExecutable"]) if d.get("LaunchExecutable") else None
        games.append(
            Game(
                id=f"epic:{app}",
                name=_clean_name(d.get("DisplayName") or app),
                source="epic",
                install_dir=install,
                launch={"uri": f"com.epicgames.launcher://apps/{ids.replace(':', '%3A')}?action=launch&silent=true"},
                exe=exe,
                icon_path=exe,
            )
        )
    return games


def scan_riot(riot_data: Path = PROGRAM_DATA / "Riot Games") -> list[Game]:
    try:
        client = json.loads((riot_data / "RiotClientInstalls.json").read_text(encoding="utf-8")).get("rc_default")
    except (OSError, ValueError):
        return []
    games = []
    for product, name in RIOT_PRODUCTS.items():
        settings = riot_data / "Metadata" / f"{product}.live" / f"{product}.live.product_settings.yaml"
        try:
            text = settings.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(r'^product_install_full_path:\s*"?([^"\n]+)"?', text, re.M)
        if not m:
            continue
        ico = settings.with_name(f"{product}.live.ico")
        games.append(
            Game(
                id=f"riot:{product}",
                name=name,
                source="riot",
                install_dir=m.group(1).strip(),
                launch={"exe": client, "args": [f"--launch-product={product}", "--launch-patchline=live"]},
                icon_path=str(ico) if ico.exists() else None,
            )
        )
    return games


def scan_xbox(roots: Iterable[Path] | None = None) -> list[Game]:
    if roots is None:
        roots = [Path(f"{d}:/XboxGames") for d in "CDEFGHIJ" if Path(f"{d}:/").exists()]
    games = []
    for root in roots:
        for config in sorted(root.glob("*/Content/MicrosoftGame.config")):
            try:
                tree = ET.parse(config).getroot()
            except (OSError, ET.ParseError):
                continue
            identity = tree.find("Identity")
            exe = tree.find("ExecutableList/Executable")
            visuals = tree.find("ShellVisuals")
            if identity is None or exe is None:
                continue  # DLC packages have no executable
            content = config.parent
            logo = visuals.get("Square150x150Logo") if visuals is not None else None
            games.append(
                Game(
                    id=f"xbox:{identity.get('Name')}",
                    name=(visuals.get("DefaultDisplayName") if visuals is not None else None) or content.parent.name,
                    source="xbox",
                    install_dir=str(content),
                    launch={"appx": identity.get("Name"), "app_id": exe.get("Id")},
                    exe=str(content / exe.get("Name", "")),
                    icon_path=str(content / logo) if logo else None,
                )
            )
    return games


RegistryEntry = dict[str, str]


def classify_uninstall(entry: RegistryEntry, battlenet_exe: str | None = None) -> Game | None:
    """Turn one uninstall-registry entry into a Game when it belongs to a known launcher."""
    name = _clean_name(entry.get("DisplayName", ""))
    uninstall = entry.get("UninstallString", "")
    install = (entry.get("InstallLocation") or "").strip().strip('"')
    icon_exe = _exe_from(entry.get("DisplayIcon", ""))
    icon = icon_exe or _exe_from(entry.get("DisplayIcon", ""), (".ico",))
    if not name:
        return None

    if m := re.search(r"Blizzard Uninstaller\.exe.*--uid=(\S+)", uninstall):
        uid = m.group(1).strip('"')
        if uid == "battle.net":
            return None
        code = BNET_CODES.get(uid)
        launch = (
            {"exe": battlenet_exe, "args": [f"--exec=launch {code}"]}
            if code and battlenet_exe
            else {"exe": icon_exe}
            if icon_exe
            else {"exe": battlenet_exe}
        )
        return Game(
            f"bnet:{uid}",
            name,
            "battlenet",
            install,
            launch,
            exe=icon_exe,
            icon_path=icon_exe,
            tags=["cod"] if "call of duty" in name.lower() else [],
        )

    if m := re.search(r"uplay://uninstall/(\d+)", uninstall):
        return Game(f"ubisoft:{m.group(1)}", name, "ubisoft", install, {"uri": f"uplay://launch/{m.group(1)}/0"}, icon_path=icon)

    if "EAInstaller" in uninstall and icon_exe:
        return Game(f"ea:{_slug(name)}", name, "ea", install, {"exe": icon_exe}, exe=icon_exe, icon_path=icon_exe)

    if "Rockstar" in entry.get("Publisher", "") and icon_exe and not re.search(r"launcher|sdk|social club", name, re.I):
        return Game(f"rockstar:{_slug(name)}", name, "rockstar", install, {"exe": icon_exe}, exe=icon_exe, icon_path=icon_exe)
    return None


def read_uninstall_registry() -> list[RegistryEntry]:  # pragma: no cover - Windows only
    if sys.platform != "win32":
        return []
    import winreg

    out = []
    hives = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, path in hives:
        try:
            root = winreg.OpenKey(hive, path)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(root)[0]):
            try:
                sub = winreg.OpenKey(root, winreg.EnumKey(root, i))
            except OSError:
                continue
            entry = {}
            for field_name in ("DisplayName", "Publisher", "InstallLocation", "UninstallString", "DisplayIcon"):
                with contextlib.suppress(OSError):
                    entry[field_name] = str(winreg.QueryValueEx(sub, field_name)[0])
            out.append(entry)
    return out


def scan_registry(entries: list[RegistryEntry] | None = None) -> list[Game]:
    entries = read_uninstall_registry() if entries is None else entries
    bnet = next((_exe_from(e.get("DisplayIcon", "")) for e in entries if e.get("DisplayName") == "Battle.net"), None)
    return [g for g in (classify_uninstall(e, bnet) for e in entries) if g]


# ── library ─────────────────────────────────────────────────────────────────

Scanner = Callable[[], list[Game]]
DEFAULT_SCANNERS: tuple[Scanner, ...] = (scan_steam, scan_epic, scan_riot, scan_xbox, scan_registry)


class Library:
    """Installed games plus user-added ones, cached to disk between scans."""

    def __init__(self, cache: Path, scanners: Iterable[Scanner] = DEFAULT_SCANNERS) -> None:
        self.cache = cache
        self.scanners = tuple(scanners)
        self.games: dict[str, Game] = {}
        self.custom: dict[str, Game] = {}
        self.hidden: set[str] = set()  # games the player hid from the library (tools, stale installs)
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.games = {g["id"]: Game(**g) for g in data.get("games", [])}
        self.custom = {g["id"]: Game(**g) for g in data.get("custom", [])}
        self.hidden = set(data.get("hidden", []))

    def _save(self) -> None:
        payload = {
            "games": [g.to_dict() for g in self.games.values()],
            "custom": [g.to_dict() for g in self.custom.values()],
            "hidden": sorted(self.hidden),
        }
        self.cache.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    def scan(self) -> list[Game]:
        found: dict[str, Game] = {}
        seen_dirs: set[str] = set()
        for scanner in self.scanners:
            try:
                results = scanner()
            except Exception:  # one broken launcher must not hide the others
                continue
            for g in results:
                if g.install_dir and not Path(g.install_dir).exists():
                    continue  # uninstalled, or the drive isn't connected
                key = _norm_dir(g.install_dir)
                if g.id in found or (key and key in seen_dirs):
                    continue  # launchers overlap (e.g. Riot games are also in the registry)
                prev = self.games.get(g.id)
                if prev and prev.steam_appid and not g.steam_appid:
                    g.steam_appid = prev.steam_appid  # keep art lookups across rescans
                found[g.id] = g
                if key:
                    seen_dirs.add(key)
        self.games = found
        self._save()
        return self.all()

    def all(self, include_hidden: bool = False) -> list[Game]:
        games = [*self.games.values(), *self.custom.values()]
        return sorted((g for g in games if include_hidden or g.id not in self.hidden), key=lambda g: g.name.lower())

    def set_hidden(self, game_id: str, hidden: bool) -> None:
        self.get(game_id)
        (self.hidden.add if hidden else self.hidden.discard)(game_id)
        self._save()

    def get(self, game_id: str) -> Game:
        g = self.games.get(game_id) or self.custom.get(game_id)
        if g is None:
            raise KeyError(game_id)
        return g

    def add_custom(self, name: str, exe: str) -> Game:
        path = Path(exe)
        if path.suffix.lower() not in (".exe", ".lnk", ".url", ".bat"):
            raise ValueError("Pick an .exe, .lnk or .url file")
        g = Game(f"custom:{_slug(name)}", name, "custom", str(path.parent), {"exe": str(path)}, exe=str(path), icon_path=str(path))
        self.custom[g.id] = g
        self._save()
        return g

    def remove_custom(self, game_id: str) -> None:
        self.custom.pop(game_id, None)
        self._save()

    def set_appid(self, game_id: str, appid: int | None) -> None:
        g = self.get(game_id)
        g.steam_appid = appid
        self._save()


def launch_command(game: Game) -> list[str] | str:
    """What to run for a game: a URI (handed to the shell) or an argv list."""
    spec = game.launch
    if "uri" in spec:
        return spec["uri"]
    if "appx" in spec:
        # MicrosoftGame.config doesn't always name the app id; the package manifest always does.
        script = (
            f"$p = Get-AppxPackage -Name '{spec['appx']}' | Select-Object -First 1; "
            "$id = @((Get-AppxPackageManifest $p).Package.Applications.Application)[0].Id; "
            'start "shell:AppsFolder\\$($p.PackageFamilyName)!$id"'
        )
        return ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script]
    if spec.get("exe"):
        return [spec["exe"], *spec.get("args", [])]
    raise ValueError(f"{game.name} has no launch method")


def launch(game: Game) -> None:  # pragma: no cover - starts real programs
    cmd = launch_command(game)
    if isinstance(cmd, str):
        os.startfile(cmd)  # type: ignore[attr-defined]  # Windows: protocol handlers (steam://, uplay://, ...)
        return
    exe = Path(cmd[0])
    if exe.suffix.lower() in (".lnk", ".url"):
        os.startfile(str(exe))  # type: ignore[attr-defined]
        return
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(cmd, cwd=str(exe.parent) if exe.parent.exists() else None, creationflags=flags, close_fds=True)


# ── helpers ─────────────────────────────────────────────────────────────────


def _steam_icon(cache: Path, appid: str) -> str | None:
    """Steam keeps each game's icon as ``librarycache/<appid>/<sha1>.jpg``."""
    folder = cache / appid
    if not folder.is_dir():
        return None
    icons = sorted(p for p in folder.glob("*.jpg") if re.fullmatch(r"[0-9a-f]{40}\.jpg", p.name))
    return str(icons[0]) if icons else None


def _exe_from(value: str, suffixes: tuple[str, ...] = (".exe",)) -> str | None:
    """``"C:\\x\\game.exe",0`` -> ``C:\\x\\game.exe`` (only for the given file types)."""
    value = value.strip()
    if not value:
        return None
    try:
        first = shlex.split(value, posix=False)[0].strip('"') if value.startswith('"') else value.split(",")[0]
    except ValueError:
        first = value
    first = first.strip().strip('"')
    return first if first.lower().endswith(suffixes) else None


def _clean_name(name: str) -> str:
    # Registry and launcher names often carry trademark symbols.
    name = re.sub("[™®©�]", "", name)
    return re.sub(r"\s{2,}", " ", name).strip()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _norm_dir(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)).rstrip("\\/") if path else ""
