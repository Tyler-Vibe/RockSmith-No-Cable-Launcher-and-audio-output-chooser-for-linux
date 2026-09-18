"""Tolerant parse/write for Rocksmith's non-standard menu JSON."""

from __future__ import annotations

import json
from pathlib import Path


def fix_game_json(raw: str) -> str:
    step1: list[str] = []
    in_str = False
    esc = False
    for char in raw:
        if esc:
            step1.append(char)
            esc = False
            continue
        if in_str:
            if char == "\\":
                esc = True
            elif char == '"':
                in_str = False
            step1.append(char)
            continue
        if char == '"':
            j = len(step1)
            while j > 0 and step1[j - 1] in " \t\n\r":
                j -= 1
            if j > 0 and step1[j - 1] in "}]":
                step1.append(",")
            in_str = True
        step1.append(char)

    text = "".join(step1)
    step2: list[str] = []
    in_str = False
    esc = False
    i = 0
    while i < len(text):
        char = text[i]
        if esc:
            step2.append(char)
            esc = False
            i += 1
            continue
        if in_str:
            if char == "\\":
                esc = True
            elif char == '"':
                in_str = False
            step2.append(char)
            i += 1
            continue
        if char == '"':
            in_str = True
            step2.append(char)
            i += 1
            continue
        if char == ",":
            j = i + 1
            while j < len(text) and text[j] in " \t\n\r":
                j += 1
            if j < len(text) and text[j] in "}]":
                i += 1
                continue
        step2.append(char)
        i += 1
    return "".join(step2)


def load_game_json(path: Path) -> dict:
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    return json.loads(fix_game_json(raw))


def dump_game_json(path: Path, data: dict) -> None:
    Path(path).write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")
