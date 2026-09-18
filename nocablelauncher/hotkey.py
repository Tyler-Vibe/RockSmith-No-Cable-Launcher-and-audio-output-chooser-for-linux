"""Optional Ctrl+M listener for the original multiplayer handshake."""

from __future__ import annotations

import threading
from typing import Callable


def start_ctrl_m_listener(callback: Callable[[], None], stop_event: threading.Event) -> threading.Thread | None:
    try:
        import evdev
        from evdev import categorize, ecodes
    except ImportError:
        return None

    devices = []
    try:
        for path in evdev.list_devices():
            device = evdev.InputDevice(path)
            caps = device.capabilities().get(ecodes.EV_KEY, [])
            if ecodes.KEY_M in caps and (
                ecodes.KEY_LEFTCTRL in caps or ecodes.KEY_RIGHTCTRL in caps
            ):
                devices.append(device)
    except (OSError, PermissionError):
        return None
    if not devices:
        return None

    def run() -> None:
        ctrl = False
        try:
            while not stop_event.is_set():
                for device in devices:
                    try:
                        for event in device.read():
                            parsed = categorize(event)
                            if not isinstance(parsed, evdev.KeyEvent):
                                continue
                            if parsed.scancode in (ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL):
                                ctrl = parsed.keystate != evdev.KeyEvent.key_up
                            elif parsed.scancode == ecodes.KEY_M and parsed.keystate == evdev.KeyEvent.key_down and ctrl:
                                callback()
                                return
                    except BlockingIOError:
                        continue
                    except OSError:
                        continue
                stop_event.wait(0.05)
        finally:
            for device in devices:
                try:
                    device.close()
                except OSError:
                    pass

    thread = threading.Thread(target=run, name="ctrl-m", daemon=True)
    thread.start()
    return thread
