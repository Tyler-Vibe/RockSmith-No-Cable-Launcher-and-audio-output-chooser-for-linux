"""Force the Proton prefix onto winealsa so capture devices enumerate."""

from __future__ import annotations

import time
from pathlib import Path


def set_prefix_audio_driver(prefix_path: str | Path, driver: str = "alsa") -> bool:
    """Write HKCU\\Software\\Wine\\Drivers Audio=alsa into user.reg."""
    if driver in ("", "default"):
        return False
    pfx = Path(prefix_path)
    user_reg = pfx / "pfx" / "user.reg" if (pfx / "pfx").is_dir() else pfx / "user.reg"
    if not user_reg.is_file():
        return False
    raw = user_reg.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16")
        encoding = "utf-16"
    else:
        text = raw.decode("utf-8", "replace")
        encoding = "utf-8"

    stamp = int(time.time())
    block = (
        f"[Software\\\\Wine\\\\Drivers] {stamp}\n"
        f'"Audio"="{driver}"\n'
    )
    key = "[Software\\\\Wine\\\\Drivers]"
    if key in text:
        start = text.find(key)
        end = text.find("\n[", start + 1)
        current = text[start:] if end < 0 else text[start:end]
        if f'"Audio"="{driver}"' in current:
            return False
        replacement = block + ("\n" if end >= 0 else "")
        text = text[:start] + replacement + (text[end:] if end >= 0 else "")
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += "\n" + block
    user_reg.write_text(text, encoding=encoding)
    return True


def _reg_multi_sz(*values: str) -> str:
    """Wine user.reg REG_MULTI_SZ (hex(7), UTF-16LE, double-null terminated)."""
    blob = bytearray()
    for value in values:
        blob.extend(value.encode("utf-16-le"))
        blob.extend(b"\x00\x00")
    blob.extend(b"\x00\x00")
    if not values:
        blob.extend(b"\x00\x00")
    return "hex(7):" + ",".join(f"{b:02x}" for b in blob)


def set_alsa_cards(
    prefix_path: str | Path,
    capture_card: str = "",
    playback_card: str = "",
    exclusive_hw: bool = True,
    default_capture_only: bool = False,
) -> bool:
    """Configure extra winealsa endpoints without duplicating the default input.

    Wine requires ALSAOutputDevices / ALSAInputDevices as REG_MULTI_SZ.
    REG_SZ values are ignored (err:alsa:get_reg_devices).
    """
    pfx = Path(prefix_path)
    user_reg = pfx / "pfx" / "user.reg" if (pfx / "pfx").is_dir() else pfx / "user.reg"
    if not user_reg.is_file():
        return False
    raw = user_reg.read_bytes()
    encoding = "utf-16" if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff") else "utf-8"
    text = raw.decode(encoding, "replace") if encoding == "utf-8" else raw.decode("utf-16")
    stamp = int(time.time())
    key = "[Software\\\\Wine\\\\Drivers\\\\winealsa.drv]"
    lines = [f"{key} {stamp}"]
    # Wine always enumerates "default" first. These are ADDITIONAL endpoints,
    # not an allowlist. pulse/hw aliases also have ROOT IDs and match 0000:0000.
    if default_capture_only:
        lines.append(f'"ALSAInputDevices"={_reg_multi_sz()}')
    elif not capture_card or not exclusive_hw:
        lines.append(f'"ALSAInputDevices"={_reg_multi_sz("pulse")}')
    if not playback_card or not exclusive_hw:
        lines.append(f'"ALSAOutputDevices"={_reg_multi_sz("pulse")}')
    lines.append('"Device"="pulse"')
    if exclusive_hw and capture_card and not default_capture_only:
        lines.append(
            f'"ALSAInputDevices"={_reg_multi_sz("pulse", f"plughw:{capture_card},0", f"hw:{capture_card}")}'
        )
    if exclusive_hw and playback_card:
        lines.append(
            f'"ALSAOutputDevices"={_reg_multi_sz("pulse", f"plughw:{playback_card},0", f"hw:{playback_card}")}'
        )
    block = "\n".join(lines) + "\n"
    if key in text:
        start = text.find(key)
        end = text.find("\n[", start + 1)
        text = text[:start] + block + ("\n" if end >= 0 else "") + (text[end:] if end >= 0 else "")
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += "\n" + block
    user_reg.write_text(text, encoding=encoding)
    return True


def set_alsa_capture_card(prefix_path: str | Path, card_id: str) -> bool:
    return set_alsa_cards(prefix_path, capture_card=card_id)


def write_pulse_asoundrc(path: Path | None = None) -> Path:
    """32-bit Wine's ALSA default is pipewire, but only amd64 ships that plugin.

    The i386 Pulse plugin is installed. Route default through Pulse → PipeWire
    (A50 / guitar via PULSE_SINK and PULSE_SOURCE).
    """
    target = path or (Path.home() / ".config" / "alsa" / "asoundrc")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# nocablelauncher — 32-bit Proton cannot load libasound_module_pcm_pipewire.so\n"
        "pcm.!default {\n"
        "    type pulse\n"
        "}\n"
        "ctl.!default {\n"
        "    type pulse\n"
        "}\n",
        encoding="utf-8",
    )
    home_rc = Path.home() / ".asoundrc"
    marker = "# nocablelauncher"
    if not home_rc.exists() or home_rc.read_text(encoding="utf-8", errors="replace").startswith(marker):
        home_rc.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def write_single_input_config(path: Path) -> Path:
    """Private ALSA namespace: only default, with no hardware-control aliases.

    Wine enumerates physical cards even when ALSAInputDevices is empty. Omitting
    ctl.hw prevents those cards being opened by winealsa; Pulse still routes the
    single default input/output using PULSE_SOURCE/PULSE_SINK.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '# NoCable game-only audio namespace; do not include system ALSA config.\n'
        'pcm.default {\n    type pulse\n}\n', encoding='utf-8',
    )
    return path
