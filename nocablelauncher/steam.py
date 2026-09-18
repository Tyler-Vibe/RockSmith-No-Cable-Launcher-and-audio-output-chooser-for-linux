"""Locate Steam, Proton, and the Rocksmith 2014 install."""

from __future__ import annotations

import os
from pathlib import Path

from . import GAME_EXE, STEAM_APP_ID


def _home() -> Path:
    return Path.home()


def candidate_steam_roots() -> list[Path]:
    env = os.environ.get("STEAM_COMPAT_CLIENT_INSTALL_PATH")
    roots: list[Path] = []
    if env:
        roots.append(Path(env))
    roots.extend(
        [
            _home() / ".steam" / "steam",
            _home() / ".steam" / "root",
            _home() / ".steam" / "debian-installation",
            _home() / ".local" / "share" / "Steam",
            Path(os.environ.get("XDG_DATA_HOME", str(_home() / ".local" / "share"))) / "Steam",
        ]
    )
    resolved: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            real = root.resolve()
        except OSError:
            continue
        if not real.exists():
            continue
        key = str(real)
        if key in seen:
            continue
        seen.add(key)
        resolved.append(real)
    return resolved


def find_steam_root() -> Path | None:
    for root in candidate_steam_roots():
        if (root / "steamapps").exists() or (root / "steam.sh").exists():
            return root
    return None


def library_folders(steam_root: Path) -> list[Path]:
    folders = [steam_root / "steamapps"]
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    if not vdf.exists():
        return [p for p in folders if p.exists()]
    text = vdf.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if '"path"' in line:
            parts = line.split('"')
            if len(parts) >= 4:
                path = Path(parts[3]) / "steamapps"
                if path.exists() and path not in folders:
                    folders.append(path)
    return [p for p in folders if p.exists()]


def find_game_exe(steam_root: Path | None = None, hint: str = "") -> Path | None:
    if hint:
        hinted = Path(hint)
        if hinted.is_file() and hinted.name.lower() == GAME_EXE.lower():
            return hinted
        nested = hinted / GAME_EXE
        if nested.is_file():
            return nested
    roots = [steam_root] if steam_root else candidate_steam_roots()
    search_dirs: list[Path] = []
    for root in roots:
        if root is None:
            continue
        for apps in library_folders(root):
            search_dirs.append(apps / "common" / "Rocksmith2014")
    seen: set[str] = set()
    for folder in search_dirs:
        try:
            real = folder.resolve()
        except OSError:
            continue
        key = str(real)
        if key in seen or not real.exists():
            continue
        seen.add(key)
        exe = real / GAME_EXE
        if exe.is_file():
            return exe
    return None


def find_prefix(steam_root: Path | None = None) -> Path | None:
    roots = [steam_root] if steam_root else candidate_steam_roots()
    for root in roots:
        if root is None:
            continue
        for apps in library_folders(root):
            prefix = apps / "compatdata" / STEAM_APP_ID
            if prefix.exists():
                return prefix
    return None


def find_proton(steam_root: Path | None = None, hint: str = "") -> Path | None:
    if hint:
        hinted = Path(hint)
        if hinted.is_file() and hinted.name == "proton":
            return hinted
        nested = hinted / "proton"
        if nested.is_file():
            return nested
    roots = [steam_root] if steam_root else candidate_steam_roots()
    preferred = (
        "Proton - Experimental",
        "Proton - Bleeding Edge",
        "Proton 10.0",
        "Proton 9.0",
        "Proton 8.0",
        "Proton 7.0",
    )
    for root in roots:
        if root is None:
            continue
        common = root / "steamapps" / "common"
        tools = root / "compatibilitytools.d"
        for name in preferred:
            proton = common / name / "proton"
            if proton.is_file():
                return proton
        if common.exists():
            for child in sorted(common.iterdir(), reverse=True):
                proton = child / "proton"
                if child.name.lower().startswith("proton") and proton.is_file():
                    return proton
        if tools.exists():
            for child in sorted(tools.iterdir(), reverse=True):
                proton = child / "proton"
                if proton.is_file():
                    return proton
    return None


def find_steam_runtime(steam_root: Path | None = None) -> Path | None:
    roots = [steam_root] if steam_root else candidate_steam_roots()
    names = (
        "SteamLinuxRuntime_sniper",
        "SteamLinuxRuntime_4",
        "SteamLinuxRuntime_soldier",
        "SteamLinuxRuntime",
    )
    for root in roots:
        if root is None:
            continue
        common = root / "steamapps" / "common"
        for name in names:
            entry = common / name / "_v2-entry-point"
            if entry.is_file():
                return entry
            run = common / name / "run"
            if run.is_file():
                return run
    return None


def find_steam_binary() -> Path | None:
    for candidate in (
        Path("/usr/games/steam"),
        Path("/usr/bin/steam"),
        _home() / ".steam" / "debian-installation" / "steam.sh",
    ):
        if candidate.exists():
            return candidate
    return None
