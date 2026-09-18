"""Enumerate Linux capture devices and resolve USB VID/PID."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CaptureDevice:
    name: str
    vid: str
    pid: str
    guid: str
    card_index: int | None = None
    alsa_id: str = ""
    usb_path: str = ""

    @property
    def label(self) -> str:
        if self.guid.startswith("pulse:"):
            return f"{self.name}  [PipeWire: {self.guid[6:]}]"
        ids = f"{self.vid}:{self.pid}"
        return f"{self.name}  [{ids}]"

    @property
    def output_label(self) -> str:
        if self.guid.startswith("pulse:"):
            return self.label
        if self.alsa_id:
            return f"{self.name}  [hw:{self.alsa_id}]"
        return self.name


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _normalize_id(value: str) -> str:
    value = value.strip().lower().replace("0x", "")
    if value.startswith("usb-"):
        return "0000"
    if not re.fullmatch(r"[0-9a-f]{1,4}", value or ""):
        return "0000"
    return value.zfill(4).upper()


def _parse_cards() -> list[tuple[int, str, str]]:
    cards_path = Path("/proc/asound/cards")
    if not cards_path.exists():
        return []
    text = cards_path.read_text(encoding="utf-8", errors="replace")
    found: list[tuple[int, str, str]] = []
    pattern = re.compile(
        r"^\s*(\d+)\s+\[([^\]]+)\]:\s+.+\n\s+(.+)$",
        re.MULTILINE,
    )
    for match in pattern.finditer(text):
        found.append((int(match.group(1)), match.group(2).strip(), match.group(3).strip()))
    return found


def _card_has_playback(index: int) -> bool:
    card_dir = Path(f"/proc/asound/card{index}")
    if not card_dir.exists():
        return False
    if any(card_dir.glob("pcm*p")):
        return True
    return Path(f"/dev/snd/pcmC{index}D0p").exists()


def _card_has_capture(index: int) -> bool:
    card_dir = Path(f"/proc/asound/card{index}")
    if not card_dir.exists():
        return False
    if any(card_dir.glob("pcm*c")):
        return True
    stream = card_dir / "stream0"
    if stream.exists() and "Capture:" in _read_text(stream):
        return True
    # USB gadget / simple adapters often only expose pcmC<N>D0c in /dev/snd
    return Path(f"/dev/snd/pcmC{index}D0c").exists()


def _card_usb_ids(index: int) -> tuple[str, str, str]:
    usbid = _read_text(Path(f"/proc/asound/card{index}/usbid"))
    if ":" in usbid:
        vid, pid = usbid.split(":", 1)
        return _normalize_id(vid), _normalize_id(pid), usbid
    return "0000", "0000", ""


def list_alsa_capture_devices() -> list[CaptureDevice]:
    devices: list[CaptureDevice] = []
    for index, short_id, long_name in _parse_cards():
        if not _card_has_capture(index):
            continue
        vid, pid, usb = _card_usb_ids(index)
        devices.append(
            CaptureDevice(
                name=long_name or short_id,
                vid=vid,
                pid=pid,
                guid=f"alsa:card{index}",
                card_index=index,
                alsa_id=short_id,
                usb_path=usb,
            )
        )
    return devices


def list_alsa_playback_devices() -> list[CaptureDevice]:
    devices: list[CaptureDevice] = []
    for index, short_id, long_name in _parse_cards():
        if not _card_has_playback(index):
            continue
        vid, pid, usb = _card_usb_ids(index)
        devices.append(
            CaptureDevice(
                name=long_name or short_id,
                vid=vid,
                pid=pid,
                guid=f"alsa:card{index}",
                card_index=index,
                alsa_id=short_id,
                usb_path=usb,
            )
        )
    return devices


def list_playback_devices() -> list[CaptureDevice]:
    """Keep each server endpoint selectable (including separate headset profiles)."""
    return list_alsa_playback_devices() + _server_devices(list_pipewire_sinks())


def _server_devices(nodes: list[dict]) -> list[CaptureDevice]:
    return [
        CaptureDevice(
            name=str(node.get("name") or node["node"]),
            vid="0000", pid="0000", guid=f"pulse:{node['node']}",
        )
        for node in nodes if node.get("node")
    ]


def list_usb_audio_devices() -> list[CaptureDevice]:
    """Fallback: USB Audio class devices from sysfs, even if ALSA is quiet."""
    devices: list[CaptureDevice] = []
    root = Path("/sys/bus/usb/devices")
    if not root.exists():
        return devices
    for node in root.iterdir():
        vendor = _read_text(node / "idVendor")
        product = _read_text(node / "idProduct")
        if not vendor or not product:
            continue
        if not any((node / child).is_dir() and (node / child / "bInterfaceClass").exists()
                   and _read_text(node / child / "bInterfaceClass") == "01"
                   for child in os.listdir(node)):
            continue
        name = _read_text(node / "product") or f"USB {vendor}:{product}"
        devices.append(
            CaptureDevice(
                name=name,
                vid=_normalize_id(vendor),
                pid=_normalize_id(product),
                guid=f"usb:{node.name}",
                usb_path=f"{vendor}:{product}",
            )
        )
    return devices


def list_pipewire_sources() -> list[dict]:
    try:
        raw = subprocess.check_output(["pw-dump"], text=True, timeout=3)
        data = json.loads(raw)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
    sources: list[dict] = []
    for item in data:
        if item.get("type") != "PipeWire:Interface:Node":
            continue
        props = (item.get("info") or {}).get("props") or {}
        media = props.get("media.class", "")
        if media not in {"Audio/Source", "Audio/Source/Virtual", "Audio/Duplex"}:
            continue
        sources.append(
            {
                "id": item.get("id"),
                "name": props.get("node.description")
                or props.get("node.nick")
                or props.get("node.name"),
                "node": props.get("node.name"),
                "card": props.get("api.alsa.pcm.card", props.get("alsa.card")),
                "vendor": props.get("device.vendor.id"),
                "product": props.get("device.product.id"),
            }
        )
    return sources


def list_pipewire_sinks() -> list[dict]:
    try:
        raw = subprocess.check_output(["pw-dump"], text=True, timeout=3)
        data = json.loads(raw)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
    sinks: list[dict] = []
    for item in data:
        if item.get("type") != "PipeWire:Interface:Node":
            continue
        props = (item.get("info") or {}).get("props") or {}
        if props.get("media.class") != "Audio/Sink":
            continue
        sinks.append(
            {
                "id": item.get("id"),
                "name": props.get("node.description")
                or props.get("node.nick")
                or props.get("node.name"),
                "node": props.get("node.name"),
                "card": props.get("api.alsa.pcm.card", props.get("alsa.card")),
            }
        )
    return sinks


def list_capture_devices() -> list[CaptureDevice]:
    devices = list_alsa_capture_devices()
    seen = {(d.vid, d.pid, d.name) for d in devices}
    for extra in list_usb_audio_devices():
        key = (extra.vid, extra.pid, extra.name)
        if extra.vid == "0000" or key in seen:
            continue
        if any(d.vid == extra.vid and d.pid == extra.pid for d in devices):
            continue
        devices.append(extra)
        seen.add(key)
    devices.extend(_server_devices(list_pipewire_sources()))
    return devices


_IGNORE_NAME_PARTS = (
    "hdmi",
    "generic",
    "webcam",
    "c920",
    "monitor",
    "dummy",
    "null",
    "a50",
    "headset",
    "headphone",
)


def is_likely_guitar_interface(device: CaptureDevice) -> bool:
    name = f"{device.name} {device.alsa_id}".lower()
    if any(part in name for part in _IGNORE_NAME_PARTS):
        return False
    return device.vid != "0000" or "usb" in name or "tonex" in name or "device" in name


def is_likely_output(device: CaptureDevice) -> bool:
    name = f"{device.name} {device.alsa_id}".lower()
    if any(part in name for part in ("hdmi", "webcam", "c920", "dummy", "null")):
        return False
    return True


def preferred_output(devices: list[CaptureDevice]) -> CaptureDevice | None:
    usable = [d for d in devices if is_likely_output(d)]
    if not usable:
        return devices[0] if devices else None
    for needle in ("a50", "headset", "headphone"):
        for device in usable:
            if needle in device.name.lower() or needle in device.alsa_id.lower():
                return device
    return usable[0]


def preferred_capture(devices: list[CaptureDevice]) -> CaptureDevice | None:
    usable = [d for d in devices if is_likely_guitar_interface(d)]
    if not usable:
        return devices[0] if devices else None
    for needle in ("tonex", "usb audio", "c-media"):
        for device in usable:
            if needle in device.name.lower() or needle in device.alsa_id.lower():
                return device
    return usable[0]


def device_by_guid(devices: list[CaptureDevice], guid: str) -> CaptureDevice | None:
    for device in devices:
        if device.guid == guid:
            return device
    return None


def _source_matches(source: dict, device: CaptureDevice) -> bool:
    if device.guid.startswith("pulse:"):
        return source.get("node") == device.guid.split(":", 1)[1]
    if device.card_index is not None and source.get("card") is not None:
        return str(source["card"]) == str(device.card_index)
    blob = " ".join(str(source.get(k) or "") for k in ("name", "node")).lower()
    if device.alsa_id and device.alsa_id.lower() in blob:
        return True
    tokens = [part for part in device.name.lower().replace(",", " ").split() if len(part) > 3]
    if device.vid != "0000" and device.vid.lower() in blob and device.pid.lower() in blob:
        return True
    return bool(tokens) and all(token in blob for token in tokens[:2])


def pulse_source_name(device: CaptureDevice) -> str | None:
    for source in list_pipewire_sources():
        if _source_matches(source, device):
            return source.get("node") or source.get("name")
    return None


def _sink_matches(sink: dict, device: CaptureDevice) -> bool:
    blob = " ".join(str(sink.get(k) or "") for k in ("name", "node")).lower()
    guid = device.guid or ""
    if guid.startswith("pulse:"):
        return sink.get("node") == guid.split(":", 1)[1]
    if device.card_index is not None and sink.get("card") is not None:
        return str(sink["card"]) == str(device.card_index)
    if device.alsa_id and device.alsa_id.lower() in blob:
        return True
    if device.name and device.name.lower() in blob:
        return True
    return False


def pulse_sink_name(device: CaptureDevice) -> str | None:
    for sink in list_pipewire_sinks():
        if _sink_matches(sink, device):
            return sink.get("node") or sink.get("name")
    return None


def _set_default(target: dict | None, kind: str) -> bool:
    if target is None:
        return False
    commands = []
    if shutil.which("wpctl"):
        commands.append(["wpctl", "set-default", str(target["id"])])
    if target.get("node") and shutil.which("pactl"):
        commands.append(["pactl", f"set-default-{kind}", target["node"]])
    succeeded = False
    for command in commands:
        try:
            result = subprocess.run(command, check=False, timeout=3,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            succeeded = result.returncode == 0 or succeeded
        except (OSError, subprocess.SubprocessError):
            continue
    return succeeded


def set_default_sink(device: CaptureDevice) -> bool:
    target = next((s for s in list_pipewire_sinks() if _sink_matches(s, device)), None)
    return _set_default(target, "sink")


def set_default_source(device: CaptureDevice) -> bool:
    target = next((s for s in list_pipewire_sources() if _source_matches(s, device)), None)
    return _set_default(target, "source")


def move_game_streams(source: str, sink: str) -> tuple[int, int, list[str]]:
    """Move only identifiable Rocksmith streams; leave other applications alone."""
    counts = [0, 0]
    errors = []
    if not shutil.which("pactl"):
        return 0, 0, ["Live switching needs pactl (pulseaudio-utils). Restart the game to apply routing."]
    for i, (kind, command, target) in enumerate((
        ("source-outputs", "move-source-output", source),
        ("sink-inputs", "move-sink-input", sink),
    )):
        if not target:
            continue
        try:
            streams = json.loads(subprocess.check_output(
                ["pactl", "--format=json", "list", kind], text=True, timeout=3,
                stderr=subprocess.DEVNULL,
            ))
            if not isinstance(streams, list):
                raise ValueError("Invalid audio stream list")
            for stream in streams:
                props = stream.get("properties") or {}
                binary = str(props.get("application.process.binary", "")).replace("\\", "/").rsplit("/", 1)[-1].lower()
                name = str(props.get("application.name", "")).lower()
                if binary != "rocksmith2014.exe" and name not in {"rocksmith2014.exe", "rocksmith 2014", "rocksmith2014"}:
                    continue
                subprocess.run(["pactl", command, str(stream["index"]), target],
                               check=True, timeout=3, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
                counts[i] += 1
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"Could not move {kind}: {exc}")
    return counts[0], counts[1], errors


def set_source_mute(guid_or_name: str, muted: bool) -> bool:
    """Best-effort hide/show of a capture source for the multiplayer trick."""
    if not guid_or_name:
        return False
    sources = list_pipewire_sources()
    target = None
    needle = guid_or_name.lower()
    for source in sources:
        blob = " ".join(str(source.get(k) or "") for k in ("name", "node")).lower()
        if needle in blob or blob in needle:
            target = source
            break
    if target is None:
        return False
    return _mute_source(target, muted)


def set_device_mute(device: CaptureDevice, muted: bool) -> bool:
    target = next((s for s in list_pipewire_sources() if _source_matches(s, device)), None)
    if target is None:
        return False
    return _mute_source(target, muted)


def hide_competing_inputs(keep: CaptureDevice, devices: list[CaptureDevice]) -> list[str]:
    """Mute other guitar-like capture devices so Rocksmith only sees one cable."""
    hidden: list[str] = []
    for device in devices:
        if device.guid == keep.guid:
            continue
        if not is_likely_guitar_interface(device):
            continue
        if set_device_mute(device, True):
            hidden.append(device.name)
    return hidden


def _mute_source(source: dict, muted: bool) -> bool:
    source_id = str(source["id"])
    try:
        result = subprocess.run(
            ["wpctl", "set-mute", source_id, "1" if muted else "0"],
            check=False,
            timeout=3,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
