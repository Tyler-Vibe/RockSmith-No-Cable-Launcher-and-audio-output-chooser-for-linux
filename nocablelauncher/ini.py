"""Apply Proton-friendly Rocksmith.ini audio defaults."""

from __future__ import annotations

from pathlib import Path

LINUX_AUDIO_DEFAULTS = {
    "EnableMicrophone": "1",
    "ExclusiveMode": "0",
    "Win32UltraLowLatencyMode": "0",
    "ForceWDM": "0",
    "ForceDirectXSink": "0",
    "RealToneCableOnly": "0",
    # Wine device names are "pulse" / "plughw:A50,0", not "A50". A forced
    # mismatch leaves Rocksmith with no playback device at all.
    "ForceDefaultPlaybackDevice": "",
}


def _ini_path(game_exe: str | Path) -> Path:
    return Path(game_exe).resolve().parent / "Rocksmith.ini"


def read_ini(path: Path) -> list[str]:
    if not path.exists():
        return [
            "[Audio]\n",
            "ExclusiveMode=0\n",
            "Win32UltraLowLatencyMode=0\n",
            "[Renderer.Win32]\n",
            "Fullscreen=2\n",
        ]
    return path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)


def apply_audio_tweaks(game_exe: str | Path, playback_device: str = "") -> Path:
    del playback_device
    path = _ini_path(game_exe)
    defaults = dict(LINUX_AUDIO_DEFAULTS)
    lines = read_ini(path)
    section = None
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            output.append(line if line.endswith("\n") else line + "\n")
            continue
        if section == "Audio" and "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in defaults:
                output.append(f"{key}={defaults[key]}\n")
                seen.add(key)
                continue
        output.append(line if line.endswith("\n") else line + "\n")

    if "[Audio]" not in "".join(output):
        output.insert(0, "[Audio]\n")
    missing = [k for k in defaults if k not in seen]
    if missing:
        patched: list[str] = []
        inserted = False
        for line in output:
            patched.append(line)
            if not inserted and line.strip() == "[Audio]":
                for key in missing:
                    patched.append(f"{key}={defaults[key]}\n")
                inserted = True
        output = patched
    path.write_text("".join(output), encoding="utf-8")
    return path
