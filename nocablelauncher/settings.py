"""Persistent launcher settings, including import of the Windows XML file."""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    path = Path(base) / "nocablelauncher"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return config_dir() / "settings.json"


@dataclass
class Settings:
    game_path: str = ""
    proton_path: str = ""
    steam_root: str = ""
    prefix_path: str = ""
    launch_method: str = "proton"  # proton | steam | wine
    is_steam: bool = True
    vid: str = "0000"
    pid: str = "0000"
    vid2: str = "0000"
    pid2: str = "0000"
    guid1: str = ""
    guid2: str = ""
    manual_dev1: bool = False
    manual_dev2: bool = False
    multiplayer: bool = False
    offset_vid: str = "012C96EC"
    offset_pid: str = "012C96F4"
    manual_offsets: bool = False
    pipewire_latency: str = "256/48000"
    use_gamemode: bool = False
    apply_ini_tweaks: bool = True
    wine_audio_driver: str = "alsa"  # alsa | pulse | default
    use_steam_runtime: bool = False
    apply_direct_connect: bool = True
    apply_memory_patch: bool = True
    proton_zero_ids: bool = True
    output_guid: str = ""
    theme: str = "dark"

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: Path | None = None) -> None:
        target = path or config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        target = path or config_path()
        data: dict = {}
        if target.exists():
            data = json.loads(target.read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def _xml_map(path: Path) -> dict[str, str]:
    root = ET.parse(path).getroot()
    values: dict[str, str] = {}
    for node in root.findall("setting"):
        name = node.attrib.get("name")
        if name:
            values[name] = (node.text or "").strip()
    return values


def import_windows_settings(xml_path: Path) -> dict:
    """Translate the original Settings.xml field names into our schema."""
    raw = _xml_map(xml_path)
    mapped = {
        "game_path": raw.get("gamePath", ""),
        "is_steam": raw.get("isSteam", "True").lower() == "true",
        "vid": raw.get("VID", "0000"),
        "pid": raw.get("PID", "0000"),
        "vid2": raw.get("VID2", "0000"),
        "pid2": raw.get("PID2", "0000"),
        "guid1": raw.get("GUID1", ""),
        "guid2": raw.get("GUID2", ""),
        "manual_dev1": raw.get("manualDev1", "False").lower() == "true",
        "manual_dev2": raw.get("manualDev2", "False").lower() == "true",
        "multiplayer": raw.get("Multiplayer", "False").lower() == "true",
        "offset_vid": raw.get("offcetVID", "012C96EC"),
        "offset_pid": raw.get("offcetPID", "012C96F4"),
        "manual_offsets": raw.get("manualOffcets", "False").lower() == "true",
    }
    if mapped["is_steam"]:
        mapped["launch_method"] = "steam"
    else:
        mapped["launch_method"] = "proton"
    return mapped


def merge_imported(settings: Settings, imported: dict) -> Settings:
    for key, value in imported.items():
        if hasattr(settings, key) and value not in (None, ""):
            setattr(settings, key, value)
    return settings
