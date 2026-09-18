"""Launch Rocksmith through Proton / Steam Linux Runtime / Wine."""

from __future__ import annotations

import os
import ctypes
import shutil
import subprocess
import time
from pathlib import Path

from . import STEAM_APP_ID
from .settings import Settings, config_dir
from .steam import find_steam_binary, find_steam_runtime
from .wine_audio import write_pulse_asoundrc, write_single_input_config


class LaunchError(RuntimeError):
    pass


def retain_wine_children() -> None:
    """Adopt Wine's detached descendants so Yama allows the launcher to patch them."""
    libc = ctypes.CDLL(None, use_errno=True)
    # PR_SET_CHILD_SUBREAPER: reparent double-forked Wine children here rather
    # than to the desktop/session reaper. This grants no extra system privileges.
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise LaunchError(f"Cannot retain Wine child processes: {os.strerror(error)}")


def _which(name: str) -> str | None:
    return shutil.which(name)


def _game_dir(settings: Settings) -> Path:
    return Path(settings.game_path).resolve().parent


def _steamapps_dir(settings: Settings) -> Path:
    game = _game_dir(settings)
    # .../steamapps/common/Rocksmith2014
    if game.parent.name == "common":
        return game.parent.parent
    if settings.steam_root:
        return Path(settings.steam_root) / "steamapps"
    return game.parent


def build_env(settings: Settings) -> dict[str, str]:
    env = os.environ.copy()
    steam_root = settings.steam_root
    prefix = settings.prefix_path
    game_dir = str(_game_dir(settings)) if settings.game_path else ""
    steamapps = str(_steamapps_dir(settings)) if settings.game_path else ""
    if steam_root:
        env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = steam_root
    if prefix:
        env["STEAM_COMPAT_DATA_PATH"] = prefix
        env["WINEPREFIX"] = str(Path(prefix) / "pfx")
    if game_dir:
        env["STEAM_COMPAT_INSTALL_PATH"] = game_dir
    if steamapps:
        env["STEAM_COMPAT_LIBRARY_PATHS"] = steamapps
    env["SteamAppId"] = STEAM_APP_ID
    env["SteamGameId"] = STEAM_APP_ID
    env["STEAM_COMPAT_APP_ID"] = STEAM_APP_ID
    env["WINEDLLOVERRIDES"] = env.get("WINEDLLOVERRIDES", "")
    env["PROTON_LOG"] = "1"
    env["PROTON_LOG_DIR"] = str(config_dir())
    if settings.pipewire_latency:
        env["PIPEWIRE_LATENCY"] = settings.pipewire_latency
        env["PULSE_LATENCY_MSEC"] = "15"
    pulse_source = getattr(settings, "pulse_source", "")
    pulse_sink = getattr(settings, "pulse_sink", "")
    if pulse_source:
        env["PULSE_SOURCE"] = pulse_source
    if pulse_sink:
        env["PULSE_SINK"] = pulse_sink
    # Never set ALSA_CARD. 32-bit default is Pulse (see write_pulse_asoundrc).
    env.pop("ALSA_CARD", None)
    if settings.apply_memory_patch and settings.proton_zero_ids and settings.wine_audio_driver == "alsa":
        audio_config = write_single_input_config(config_dir() / "game-audio.conf")
        env["ALSA_CONFIG_PATH"] = str(audio_config)
    else:
        write_pulse_asoundrc()
    return env


def wrap_with_helpers(command: list[str], settings: Settings) -> list[str]:
    if settings.use_gamemode and _which("gamemoderun"):
        command = ["gamemoderun", *command]
    return command


def proton_command(settings: Settings, extra_exe: str | None = None) -> list[str]:
    proton = Path(settings.proton_path)
    exe = extra_exe or settings.game_path
    if not proton.is_file():
        raise LaunchError(f"Proton script not found: {proton}")
    if not Path(exe).is_file():
        raise LaunchError(f"Game executable not found: {exe}")
    command = [str(proton), "waitforexitandrun", str(exe)]
    # Steam Linux Runtime (bwrap) blocks ptrace, so the cable patch cannot
    # read game memory. Only wrap it when the user explicitly asks.
    if settings.use_steam_runtime:
        runtime = find_steam_runtime(Path(settings.steam_root) if settings.steam_root else None)
        if runtime:
            command = [str(runtime), "--verb=waitforexitandrun", "--", *command]
    return wrap_with_helpers(command, settings)


def _steam_client_env() -> dict[str, str]:
    """Steam refuses to show a window if it inherits Proton/game session vars."""
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("STEAM_COMPAT") or key.startswith("SteamApp") or key.startswith("SteamGame"):
            env.pop(key, None)
    for key in ("WINEPREFIX", "PROTON_LOG", "PROTON_LOG_DIR", "WINEDLLOVERRIDES"):
        env.pop(key, None)
    return env


def steam_command() -> list[str]:
    steam = find_steam_binary()
    if steam is None:
        opener = _which("xdg-open")
        if opener:
            return [opener, f"steam://rungameid/{STEAM_APP_ID}"]
        raise LaunchError("Steam executable not found.")
    return [str(steam), f"steam://rungameid/{STEAM_APP_ID}"]


def wine_command(settings: Settings) -> list[str]:
    wine = None
    if settings.proton_path:
        candidate = Path(settings.proton_path).parent / "files" / "bin" / "wine"
        if candidate.is_file():
            wine = candidate
    if wine is None:
        found = _which("wine")
        if found:
            wine = Path(found)
    if wine is None:
        raise LaunchError("Wine binary not found.")
    if not Path(settings.game_path).is_file():
        raise LaunchError(f"Game executable not found: {settings.game_path}")
    return wrap_with_helpers([str(wine), settings.game_path], settings)


def _log_path() -> Path:
    return config_dir() / "launch.log"


def _tail(path: Path, lines: int = 40) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


def launch_game(settings: Settings, log=None) -> subprocess.Popen:
    method = settings.launch_method
    if method != "steam" and settings.apply_memory_patch:
        retain_wine_children()
    if method == "steam":
        env = _steam_client_env()
        command = steam_command()
        cwd = None
    elif method == "wine":
        env = build_env(settings)
        command = wine_command(settings)
        cwd = str(_game_dir(settings))
    else:
        env = build_env(settings)
        command = proton_command(settings)
        cwd = str(_game_dir(settings))
    if log:
        log("Launch command: " + " ".join(command))
    log_file = _log_path()
    handle = log_file.open("w", encoding="utf-8")
    handle.write(" ".join(command) + "\n")
    handle.flush()
    proc = subprocess.Popen(
        command,
        env=env,
        cwd=cwd,
        stdout=handle,
        stderr=subprocess.STDOUT,
        start_new_session=method == "steam",
    )
    proc._ncl_log = handle  # keep the log handle alive  # noqa: SLF001
    # Steam returns immediately; Proton/Wine should stay alive.
    if method != "steam":
        time.sleep(2.5)
        code = proc.poll()
        if code is not None:
            handle.flush()
            detail = _tail(log_file) or f"exit {code}"
            if method == "proton" and find_steam_binary():
                if log:
                    log(f"Direct Proton failed ({code}). Falling back to Steam Play.")
                    log(detail)
                return launch_game(
                    Settings(**{**settings.to_dict(), "launch_method": "steam"}),
                    log=log,
                )
            raise LaunchError(f"Proton exited immediately ({code}). {detail}")
    if log:
        log(f"Launcher pid={proc.pid}. Full log: {log_file}")
    return proc


def wrap_and_exec(argv: list[str], settings: Settings) -> subprocess.Popen:
    """Used as a Steam launch-option wrapper: nocable-launcher --wrap -- %command%."""
    if not argv:
        raise LaunchError("Wrap mode received an empty command.")
    if settings.apply_memory_patch:
        retain_wine_children()
    env = build_env(settings)
    if settings.pipewire_latency:
        env["PIPEWIRE_LATENCY"] = settings.pipewire_latency
    return subprocess.Popen(argv, env=env, start_new_session=False)
