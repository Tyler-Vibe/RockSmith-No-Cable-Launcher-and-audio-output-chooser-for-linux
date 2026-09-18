"""Enable Rocksmith's hidden Direct Connect input mode (RSMods method)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from . import game_json
from . import psarc

STARTUP_NAME = "ui_menu_pillar_startup.database.json"
MISSION_NAME = "ui_menu_pillar_mission.database.json"


class DirectConnectError(RuntimeError):
    pass


def _find_manifest(root: Path, name: str) -> Path:
    matches = list(root.rglob(name))
    if not matches:
        raise DirectConnectError(f"{name} not found in extracted cache")
    return matches[0]


def _input_select_definition(data: dict) -> dict | None:
    entries = data.get("Static", {}).get("UI", {}).get("Menus", {}).get("Entries", {})
    node = entries.get("FE_InputSelect")
    if not node:
        return None
    return node.get("View", {}).get("Definition")


def startup_has_othercable(data: dict) -> bool:
    definition = _input_select_definition(data)
    if not definition:
        return False
    return "othercable" in definition.get("Images", {}) and "othercable" in definition.get("Buttons", {})


def mission_has_direct_connect(data: dict) -> bool:
    buttons = data["Static"]["UI"]["Menus"]["Entries"]["MissionMenu"]["View"]["Definition"]["Buttons"]
    return "3" in buttons["inputMode"]["AcceptedValues"]


def add_startup_othercable(data: dict) -> dict:
    definition = _input_select_definition(data)
    if definition is None:
        raise DirectConnectError(
            "This cache.psarc has no FE_InputSelect menu (older / non-Remastered dump). "
            "Use the RealTone memory patch instead."
        )
    definition.setdefault("Images", {})["othercable"] = {
        "ID": "othercable",
        "File": "inputmode_directconnect.png",
        "InputModeIconFileText": "Directcon_",
    }
    definition.setdefault("Buttons", {})["othercable"] = {
        "ID": "othercable",
        "Label": "DIRECT CONNECT",
        "Description": (
            "$[37291]Use a cable other than the Real Tone Cable to plug in "
            "your electric guitar or bass."
        ),
        "State": "up",
        "SortOrder": 1,
    }
    return data


def add_mission_direct_connect(data: dict) -> dict:
    accepted = data["Static"]["UI"]["Menus"]["Entries"]["MissionMenu"]["View"]["Definition"][
        "Buttons"
    ]["inputMode"]["AcceptedValues"]
    accepted["3"] = "DIRECT CONNECT"
    return data


def _bundled_7z() -> str | None:
    here = Path(__file__).resolve().parent.parent / "vendor" / "7zip" / "7z"
    if here.is_file():
        return str(here)
    return shutil.which("7z") or shutil.which("7zz") or shutil.which("7za")


def _update_7z(archive: Path, files: list[Path], workdir: Path) -> None:
    try:
        import py7zr
    except ImportError:
        py7zr = None
    if py7zr is not None:
        with py7zr.SevenZipFile(archive, "a") as sz:
            for path in files:
                sz.write(path, str(path.relative_to(workdir)))
        return
    seven = _bundled_7z()
    if not seven:
        raise DirectConnectError("Need python3-py7zr or the 7z/7zz binary to edit cache7.7z")
    import subprocess

    cmd = [seven, "u", str(archive), *[str(p.relative_to(workdir)) for p in files], "-bso0"]
    subprocess.run(cmd, cwd=workdir, check=True)


def _extract_7z_manifests(archive: Path, dest: Path) -> None:
    try:
        import py7zr
    except ImportError:
        py7zr = None
    targets = [f"manifests/{STARTUP_NAME}", f"manifests/{MISSION_NAME}"]
    if py7zr is not None:
        with py7zr.SevenZipFile(archive, "r") as sz:
            names = set(sz.getnames())
            wanted = [name for name in targets if name in names]
            if not wanted:
                wanted = [n for n in names if n.endswith(STARTUP_NAME) or n.endswith(MISSION_NAME)]
            sz.extract(path=dest, targets=wanted)
        return
    seven = _bundled_7z()
    if not seven:
        raise DirectConnectError("Need python3-py7zr or the 7z/7zz binary to edit cache7.7z")
    import subprocess

    subprocess.run(
        [seven, "x", str(archive), *targets, f"-o{dest}", "-y", "-bso0"],
        check=True,
    )


def is_applied(game_dir: Path) -> bool:
    marker = game_dir / "cache.psarc.directconnect"
    return marker.exists()


def apply(game_dir: Path, log=None) -> bool:
    """Patch cache.psarc. Returns True if a change was written."""

    def say(message: str) -> None:
        if log:
            log(message)

    game_dir = Path(game_dir)
    cache = game_dir / "cache.psarc"
    if not cache.is_file():
        raise DirectConnectError(f"cache.psarc not found in {game_dir}")

    backup = game_dir / "cache.psarc.bak"
    if not backup.exists():
        shutil.copy2(cache, backup)
        say(f"Backed up cache.psarc to {backup}")

    with tempfile.TemporaryDirectory(prefix="ncl-dc-") as tmp:
        tmp_path = Path(tmp)
        psarc_dir = tmp_path / "psarc"
        psarc_dir.mkdir()
        say("Extracting cache.psarc...")
        psarc.extract(cache, psarc_dir)
        archive = psarc_dir / "cache7.7z"
        if not archive.is_file():
            # Some dumps store the 7z under another name.
            found = list(psarc_dir.rglob("*.7z"))
            if not found:
                raise DirectConnectError("cache7.7z not found inside cache.psarc")
            archive = found[0]

        _extract_7z_manifests(archive, tmp_path)
        startup = _find_manifest(tmp_path, STARTUP_NAME)
        mission = _find_manifest(tmp_path, MISSION_NAME)
        startup_data = game_json.load_game_json(startup)
        mission_data = game_json.load_game_json(mission)
        if _input_select_definition(startup_data) is None:
            say(
                "This install's cache.psarc is the older 2014 menu set — no Direct Connect screen. "
                "Falling back to the RealTone VID/PID memory patch."
            )
            return False
        if startup_has_othercable(startup_data) and mission_has_direct_connect(mission_data):
            (game_dir / "cache.psarc.directconnect").write_text("1\n", encoding="utf-8")
            say("Direct Connect is already enabled in cache.psarc")
            return False

        add_startup_othercable(startup_data)
        add_mission_direct_connect(mission_data)
        game_json.dump_game_json(startup, startup_data)
        game_json.dump_game_json(mission, mission_data)
        _update_7z(archive, [startup, mission], tmp_path)

        rebuilt = tmp_path / "cache.psarc.new"
        say("Repacking cache.psarc...")
        psarc.repack(psarc_dir, rebuilt)
        shutil.copy2(rebuilt, cache)
        (game_dir / "cache.psarc.directconnect").write_text("1\n", encoding="utf-8")
        say("Direct Connect enabled. In game: Path / Input → Direct Connect.")
        return True
