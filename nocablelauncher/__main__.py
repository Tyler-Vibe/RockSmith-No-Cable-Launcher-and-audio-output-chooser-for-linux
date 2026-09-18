"""CLI entry: python3 -m nocablelauncher [--set] [--wrap -- %command%]."""

from __future__ import annotations

import argparse
import sys
from .settings import Settings, import_windows_settings, merge_imported
from .steam import find_game_exe, find_steam_root


def _bootstrap_settings() -> Settings:
    settings = Settings.load()
    steam = find_steam_root()
    exe = find_game_exe(steam, settings.game_path)
    if exe:
        xml = exe.parent / "Settings.xml"
        if xml.exists() and not settings.game_path:
            merge_imported(settings, import_windows_settings(xml))
        if not settings.game_path or settings.game_path.startswith("C:"):
            settings.game_path = str(exe)
    return settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Linux NoCable Launcher — play Rocksmith 2014 without a RealTone cable."
    )
    parser.add_argument(
        "-set",
        "--set",
        action="store_true",
        help="Open the settings window (default action; kept for EditSettings.bat compatibility).",
    )
    parser.add_argument(
        "--wrap",
        action="store_true",
        help="Steam launch-option wrapper. Put the real command after --",
    )
    parser.add_argument(
        "--apply-direct-connect",
        action="store_true",
        help="Patch cache.psarc for Direct Connect and exit (no GUI).",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command after -- for wrap mode")
    args = parser.parse_args(argv)

    if args.apply_direct_connect:
        settings = _bootstrap_settings()
        from pathlib import Path

        from .direct_connect import apply as apply_dc
        from .ini import apply_audio_tweaks
        from .wine_audio import set_prefix_audio_driver

        if settings.game_path:
            apply_audio_tweaks(settings.game_path)
            apply_dc(Path(settings.game_path).parent, log=print)
        if settings.prefix_path:
            set_prefix_audio_driver(settings.prefix_path, settings.wine_audio_driver)
        return 0

    wrap_argv: list[str] | None = None
    if args.wrap:
        command = list(args.command)
        if command and command[0] == "--":
            command = command[1:]
        wrap_argv = command

    from .app import run_gui

    return run_gui(_bootstrap_settings(), wrap_argv=wrap_argv)


if __name__ == "__main__":
    sys.exit(main())
