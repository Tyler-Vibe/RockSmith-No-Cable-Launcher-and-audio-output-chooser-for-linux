"""Runtime VID/PID patch — same algorithm as the original Windows launcher."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import re
import signal
import struct
import time
from dataclasses import dataclass
from pathlib import Path

from . import CABLE_PATTERN, GAME_EXE, GAME_PROCESS, REALTONE_PID, REALTONE_VID


class PatchError(RuntimeError):
    pass


def hex_to_int(value: str) -> int:
    value = value.strip()
    if value.lower().startswith("0x"):
        value = value[2:]
    return int(value, 16)


def device_id_bytes(value: str) -> bytes:
    """Convert a 4-digit hex USB ID to the little-endian two-byte form the game uses."""
    value = value.strip()
    if value.lower().startswith("0x"):
        value = value[2:]
    value = value.zfill(4)
    return bytes((int(value[2:4], 16), int(value[0:2], 16)))


def find_pattern(buffer: bytes, pattern: bytes = CABLE_PATTERN) -> int | None:
    if len(pattern) > len(buffer):
        return None
    index = buffer.find(pattern) if pattern else -1
    return index if index >= 0 else None


@dataclass
class ProcessModule:
    pid: int
    start: int
    size: int
    path: str


_LIBC = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


class _IOVec(ctypes.Structure):
    _fields_ = [("iov_base", ctypes.c_void_p), ("iov_len", ctypes.c_size_t)]


_LIBC.process_vm_readv.argtypes = [
    ctypes.c_int,
    ctypes.POINTER(_IOVec),
    ctypes.c_ulong,
    ctypes.POINTER(_IOVec),
    ctypes.c_ulong,
    ctypes.c_ulong,
]
_LIBC.process_vm_readv.restype = ctypes.c_ssize_t
_LIBC.process_vm_writev.argtypes = _LIBC.process_vm_readv.argtypes
_LIBC.process_vm_writev.restype = ctypes.c_ssize_t


def _vm_rw(pid: int, address: int, data: bytes, write: bool) -> bytes:
    size = len(data)
    buf = ctypes.create_string_buffer(data if write else size, size)
    local = _IOVec(ctypes.cast(buf, ctypes.c_void_p), size)
    remote = _IOVec(ctypes.c_void_p(address), size)
    fn = _LIBC.process_vm_writev if write else _LIBC.process_vm_readv
    n = fn(pid, ctypes.byref(local), 1, ctypes.byref(remote), 1, 0)
    if n != size:
        err = ctypes.get_errno()
        action = "write" if write else "read"
        raise PatchError(
            f"Cannot {action} {size} bytes at {address:#x} in pid {pid} "
            f"(errno {err}). Launch through this app or Steam wrap mode so "
            f"the game is a child process (ptrace_scope=1)."
        )
    return buf.raw


def read_memory(pid: int, address: int, size: int) -> bytes:
    return _vm_rw(pid, address, b"\x00" * size, write=False)


def write_memory(pid: int, address: int, data: bytes) -> None:
    _vm_rw(pid, address, data, write=True)


def _cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", "replace")


def _comm(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


_WRAPPER_MARKERS = (
    "bwrap",
    "pressure-vessel",
    "pv-adverb",
    "proton",
    "python",
    "gamemoderun",
    "steam-runtime",
    "steam.exe",
    "winedevice",
    "wineserver",
    "xalia",
    "_v2-entry-point",
    "nocable",
)


def _is_wrapper(comm: str, cmd: str) -> bool:
    blob = f"{comm} {cmd}".lower()
    return any(marker in blob for marker in _WRAPPER_MARKERS)


def find_game_pids() -> list[int]:
    """Return Wine game PIDs, excluding Proton/bwrap wrappers."""
    ranked: list[tuple[int, int]] = []
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        comm = _comm(pid)
        cmd = _cmdline(pid)
        if GAME_PROCESS not in comm and GAME_EXE not in cmd and GAME_PROCESS + ".exe" not in cmd:
            continue
        if _is_wrapper(comm, cmd):
            continue
        score = 0
        if comm.startswith(GAME_PROCESS):
            score += 2
        if cmd.strip().endswith(GAME_EXE) or GAME_EXE in cmd.split("\\")[-1]:
            score += 1
        ranked.append((score, pid))
    ranked.sort(reverse=True)
    return [pid for _score, pid in ranked]


def find_stale_proton_pids() -> list[int]:
    """Proton/gamemode waiters left behind by a previous Launch click."""
    found: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        cmd = _cmdline(pid)
        if "waitforexitandrun" in cmd and GAME_EXE in cmd:
            found.append(pid)
    return found


def stop_stale_game(timeout: float = 8.0) -> list[int]:
    """Quit leftover Rocksmith/Proton so the next launch is a readable child."""
    pids = sorted(set(find_game_pids() + find_stale_proton_pids()))
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(Path(f"/proc/{pid}").exists() for pid in pids):
            break
        time.sleep(0.2)
    still = [pid for pid in pids if Path(f"/proc/{pid}").exists()]
    for pid in still:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            continue
    return pids


def choose_game_pid(
    pids: list[int],
    ignore: set[int],
    access: dict[int, str],
) -> int | None:
    """Pick the first game we can read, skipping leftovers from an earlier launch."""
    for pid in pids:
        if pid in ignore:
            continue
        if access.get(pid) == "ok":
            return pid
    return None


def wait_for_game(
    timeout: float = 90.0,
    poll: float = 0.5,
    ignore_pids: set[int] | None = None,
) -> int:
    ignore = set(ignore_pids or [])
    deadline = time.time() + timeout
    blocked: list[int] = []
    while time.time() < deadline:
        access = {pid: probe_process_access(pid) for pid in find_game_pids()}
        chosen = choose_game_pid(list(access), ignore, access)
        if chosen is not None:
            return chosen
        blocked.extend(pid for pid, state in access.items() if state == "blocked" and pid not in ignore)
        time.sleep(poll)
    if blocked:
        shown = ", ".join(str(pid) for pid in sorted(set(blocked)))
        raise PatchError(
            f"Rocksmith is already running (pid {shown}) but this launcher cannot "
            "read it (ptrace). Quit every Rocksmith and Proton window, then Launch "
            "once from this app — do not click Launch while an old copy is still open."
        )
    raise PatchError(f"Timed out waiting for {GAME_EXE} ({timeout:.0f}s).")


_MAP_RE = re.compile(
    r"^([0-9a-fA-F]+)-([0-9a-fA-F]+)\s+(\S+)\s+\S+\s+\S+\s+\S+\s*(.*)$"
)


def parse_maps(pid: int) -> list[tuple[int, int, str, str]]:
    maps: list[tuple[int, int, str, str]] = []
    try:
        text = Path(f"/proc/{pid}/maps").read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise PatchError(f"Cannot read /proc/{pid}/maps: {exc}") from exc
    for line in text.splitlines():
        match = _MAP_RE.match(line)
        if not match:
            continue
        start = int(match.group(1), 16)
        end = int(match.group(2), 16)
        maps.append((start, end, match.group(3), match.group(4)))
    return maps


def locate_module(pid: int) -> ProcessModule:
    """Read PE SizeOfImage, including Wine's anonymous unpacked sections."""
    maps = parse_maps(pid)
    headers = [m for m in maps if GAME_EXE.lower() in m[3].lower() and "r" in m[2]]
    for start, _end, _perms, path in headers:
        try:
            dos = read_memory(pid, start, 64)
            if dos[:2] != b"MZ":
                continue
            pe_offset = struct.unpack_from("<I", dos, 0x3C)[0]
            if not 64 <= pe_offset < 1024 * 1024:
                continue
            pe = read_memory(pid, start + pe_offset, 84)
            if pe[:4] != b"PE\0\0" or struct.unpack_from("<H", pe, 24)[0] != 0x10B:
                continue
            size = struct.unpack_from("<I", pe, 80)[0]
            if not 4096 <= size <= 256 * 1024 * 1024:
                continue
            return ProcessModule(pid, start, size, path)
        except (PatchError, struct.error):
            continue
    raise PatchError(f"Could not validate {GAME_EXE} PE image in pid {pid}.")


def _readable_regions(pid: int) -> list[tuple[int, int]]:
    module = locate_module(pid)
    image_end = module.start + module.size
    return [
        (max(start, module.start), min(end, image_end))
        for start, end, perms, _path in parse_maps(pid)
        if "r" in perms and start < image_end and end > module.start
    ]


def _find_ids_in_blob(blob: bytes, base: int) -> list[tuple[int, int]]:
    """Require the full cable table signature; two USB IDs alone are ambiguous."""
    found = []
    index = 0
    while True:
        hit = blob.find(CABLE_PATTERN, index)
        if hit < 0:
            return found
        found.append((base + hit, base + hit + len(CABLE_PATTERN) - 2))
        index = hit + 1


def probe_process_access(pid: int) -> str:
    """Return ok, blocked (EPERM), or not_ready (mapped but unread / still starting)."""
    try:
        maps = parse_maps(pid)
    except PatchError:
        return "blocked"
    for start, end, perms, _path in maps:
        if "r" not in perms or end <= start:
            continue
        try:
            read_memory(pid, start, min(2, end - start))
            return "ok"
        except PatchError as exc:
            msg = str(exc)
            if "errno 1" in msg or "errno 13" in msg:
                return "blocked"
            continue
    return "not_ready"


def can_read_process(pid: int) -> bool:
    return probe_process_access(pid) == "ok"


def scan_for_offsets(pid: int) -> tuple[int, int]:
    found = scan_all_offsets(pid)
    if not found:
        raise PatchError("RealTone cable signature not found in game memory.")
    if len(found) != 1:
        raise PatchError(f"Found {len(found)} cable tables inside the game image; refusing an ambiguous patch.")
    return found[0]


def scan_all_offsets(pid: int) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    seen: set[int] = set()
    regions = _readable_regions(pid)
    read_ok = 0
    read_fail = 0
    last_err = ""
    for start, end in regions:
        size = end - start
        try:
            blob = read_memory(pid, start, size)
        except PatchError as exc:
            read_fail += 1
            last_err = str(exc)
            continue
        read_ok += 1
        for vid, pid_addr in _find_ids_in_blob(blob, start):
            if vid not in seen:
                found.append((vid, pid_addr))
                seen.add(vid)
    if read_ok == 0:
        raise PatchError(
            "Cannot read Rocksmith memory (ptrace). An old Rocksmith/Proton "
            "copy is probably still running. Quit every game window, then Launch "
            "once from this app. "
            + (last_err or "")
        )
    if not found and read_fail:
        raise PatchError(
            f"RealTone signature not found ({read_ok} readable regions, "
            f"{read_fail} unreadable)."
        )
    return found


def offsets_look_valid(pid: int, offset_vid: int, offset_pid: int) -> bool:
    try:
        module = locate_module(pid)
        if offset_pid != offset_vid + len(CABLE_PATTERN) - 2:
            return False
        if not module.start <= offset_vid <= module.start + module.size - len(CABLE_PATTERN):
            return False
        return read_memory(pid, offset_vid, len(CABLE_PATTERN)) == CABLE_PATTERN
    except PatchError:
        return False


def apply_patch(pid: int, vid: str, pid_hex: str, offset_vid: int, offset_pid: int) -> None:
    expected_vid, expected_pid = device_id_bytes(vid), device_id_bytes(pid_hex)
    write_memory(pid, offset_vid, expected_vid)
    write_memory(pid, offset_pid, expected_pid)
    if read_memory(pid, offset_vid, 2) != expected_vid or read_memory(pid, offset_pid, 2) != expected_pid:
        raise PatchError("Cable patch read-back did not match the requested IDs.")


def patch_game(
    vid: str,
    pid_hex: str,
    offset_vid_hex: str,
    offset_pid_hex: str,
    manual_offsets: bool = False,
    game_pid: int | None = None,
    timeout: float = 90.0,
    ignore_pids: set[int] | None = None,
    log=None,
) -> tuple[int, str, str]:
    """Wait for Rocksmith, resolve offsets, write player device IDs.

    Returns (pid, offset_vid_hex, offset_pid_hex) after a successful patch.
    """

    def say(message: str) -> None:
        if log:
            log(message)

    if game_pid is None:
        say(f"Waiting for {GAME_EXE}...")
        game_pid = wait_for_game(timeout=timeout, ignore_pids=ignore_pids)
    say(f"Found game process pid={game_pid} ({_comm(game_pid)})")

    offset_vid = hex_to_int(offset_vid_hex)
    offset_pid = hex_to_int(offset_pid_hex)
    deadline = time.time() + min(timeout, 75)
    last_error = None
    while time.time() < deadline:
        if offsets_look_valid(game_pid, offset_vid, offset_pid):
            say(f"Using offsets VID={offset_vid_hex} PID={offset_pid_hex}")
            break
        if manual_offsets:
            raise PatchError(
                "Configured offsets do not contain the RealTone IDs. "
                "Uncheck manual offsets to scan automatically."
            )
        say("Cable signature not ready yet (packed exe). Scanning memory...")
        try:
            offset_vid, offset_pid = scan_for_offsets(game_pid)
            offset_vid_hex = f"{offset_vid:08X}"
            offset_pid_hex = f"{offset_pid:08X}"
            say(f"Found offsets VID={offset_vid_hex} PID={offset_pid_hex}")
            break
        except PatchError as exc:
            last_error = exc
            if "ptrace blocked" in str(exc):
                raise
            time.sleep(1.5)
    else:
        raise PatchError(
            str(last_error or "RealTone cable signature not found in game memory.")
        )

    apply_patch(game_pid, vid, pid_hex, offset_vid, offset_pid)
    say(f"Verified cable table patch VID={vid} PID={pid_hex} at {offset_vid:#x}/{offset_pid:#x}")

    # Restamp only this verified table if unpacking restores its original bytes.
    expected = device_id_bytes(vid) + CABLE_PATTERN[2:-2] + device_id_bytes(pid_hex)
    for _ in range(8):
        time.sleep(0.4)
        if not process_running(game_pid):
            raise PatchError("Rocksmith exited before cable patch verification finished.")
        current = read_memory(game_pid, offset_vid, len(CABLE_PATTERN))
        if current == CABLE_PATTERN:
            apply_patch(game_pid, vid, pid_hex, offset_vid, offset_pid)
        elif current != expected:
            raise PatchError("Cable table changed unexpectedly after patching; restart Rocksmith.")
    return game_pid, offset_vid_hex, offset_pid_hex


def process_running(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists()
