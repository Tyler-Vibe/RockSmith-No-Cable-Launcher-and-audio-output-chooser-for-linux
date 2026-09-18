"""GTK settings + launch window."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango

from . import APP_NAME, STEAM_APP_ID, __version__
from .devices import (
    CaptureDevice,
    device_by_guid,
    list_alsa_playback_devices,
    list_capture_devices,
    list_playback_devices,
    preferred_capture,
    preferred_output,
    pulse_sink_name,
    pulse_source_name,
    move_game_streams,
    set_default_sink,
    set_default_source,
    set_device_mute,
    set_source_mute,
)
from .hotkey import start_ctrl_m_listener
from .direct_connect import DirectConnectError, apply as apply_direct_connect
from .ini import apply_audio_tweaks
from .patcher import PatchError, find_game_pids, patch_game, process_running, stop_stale_game
from .proton import LaunchError, launch_game, wrap_and_exec
from .settings import Settings, config_dir, config_path, import_windows_settings, merge_imported
from .wine_audio import set_alsa_cards, set_prefix_audio_driver, write_pulse_asoundrc
from .steam import (
    find_game_exe,
    find_prefix,
    find_proton,
    find_steam_root,
)

LIGHT_CSS = """
window {
  background-color: #f2f2f2;
  color: #181818;
}
headerbar { background-color: #e5e5e5; color: #181818; }
label, entry, button, combobox, menuitem { color: #181818; }
.ncl-title { font-size: 20px; font-weight: bold; color: #181818; }
.ncl-sub { color: #353535; }
entry, button, combobox button, menu {
  background-image: none;
  background-color: #ffffff;
  color: #181818;
}
button:hover { background-color: #e3e3e3; }
button:active, button:checked { background-color: #d3d3d3; }
menuitem:hover { background-color: #d3d3d3; }
.ncl-log, .ncl-log text {
  background-color: #ffffff;
  color: #181818;
  font-family: monospace;
  font-size: 11px;
}
entry, combobox { min-height: 28px; }
button.suggested-action { font-weight: bold; color: #181818; }
combobox.audio-device, combobox.audio-device button,
combobox.audio-device cellview, combobox.audio-device cellview:selected,
combobox.audio-device cellview:hover {
  color: #080808;
  text-shadow: none;
  font-weight: 600;
}
combobox.audio-device menuitem, combobox.audio-device menuitem:hover,
combobox.audio-device menuitem:selected {
  color: #080808;
  background-color: #ffffff;
}

"""

DARK_CSS = LIGHT_CSS
for light, dark in {
    "#f2f2f2": "#16191f", "#181818": "#f5f7fa",
    "#e5e5e5": "#20252d", "#353535": "#d0d7e2",
    "#ffffff": "#252c36", "#e3e3e3": "#354051",
    "#d3d3d3": "#43516a", "#080808": "#ffffff",
}.items():
    DARK_CSS = DARK_CSS.replace(light, dark)
CSS = DARK_CSS



class LauncherWindow(Gtk.Window):
    def __init__(self, settings: Settings):
        super().__init__(title=f"{APP_NAME} for Linux")
        self.set_default_size(780, 720)
        self.settings = settings
        self.devices: list[CaptureDevice] = []
        self.outputs: list[CaptureDevice] = []
        self._launching = False
        self._game_pid: int | None = None
        self._stop_hotkey = threading.Event()
        self._hotkey_thread = None

        self.theme_provider = Gtk.CssProvider()
        provider = self.theme_provider
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self._build()
        self._apply_theme()
        self.autodetect()
        self.refresh_devices()
        self._load_into_widgets()
        self.connect("destroy", self._on_destroy)

    def _build(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_border_width(14)
        page_scroll = Gtk.ScrolledWindow()
        page_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        page_scroll.add(outer)
        self.add(page_scroll)

        title = Gtk.Label(label=APP_NAME)
        title.get_style_context().add_class("ncl-title")
        title.set_xalign(0)
        sub = Gtk.Label(
            label="Play Rocksmith 2014 without a RealTone cable, through Proton / Steam Linux Runtime"
        )
        sub.get_style_context().add_class("ncl-sub")
        sub.set_xalign(0)
        sub.set_line_wrap(True)
        title_row = Gtk.Box(spacing=12)
        title_row.pack_start(title, True, True, 0)
        self.theme_button = Gtk.Button()
        self.theme_button.connect("clicked", self._toggle_theme)
        title_row.pack_end(self.theme_button, False, False, 0)
        outer.pack_start(title_row, False, False, 0)
        outer.pack_start(sub, False, False, 0)

        grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        outer.pack_start(grid, False, False, 6)
        row = 0

        self.game_entry = Gtk.Entry()
        row = self._path_row(grid, row, "Game exe", self.game_entry, self._browse_game)
        self.proton_entry = Gtk.Entry()
        row = self._path_row(grid, row, "Proton", self.proton_entry, self._browse_proton)
        self.steam_entry = Gtk.Entry()
        row = self._path_row(grid, row, "Steam root", self.steam_entry, None)
        self.prefix_entry = Gtk.Entry()
        row = self._path_row(grid, row, "Proton prefix", self.prefix_entry, None)

        self.method_combo = Gtk.ComboBoxText()
        self.method_combo.append("proton", "Proton + Steam Linux Runtime (recommended)")
        self.method_combo.append("steam", "Ask Steam to open the game")
        self.method_combo.append("wine", "Proton Wine binary only")
        self.method_combo.set_active_id("proton")
        grid.attach(Gtk.Label(label="Launch via", xalign=1), 0, row, 1, 1)
        grid.attach(self.method_combo, 1, row, 2, 1)
        row += 1

        self.dc_check = Gtk.CheckButton(
            label="Enable Direct Connect as an alternative in Path / Input"
        )
        self.dc_check.set_active(True)
        outer.pack_start(self.dc_check, False, False, 0)
        self.memory_check = Gtk.CheckButton(label="Emulate RealTone cable using the selected input")
        outer.pack_start(self.memory_check, False, False, 0)
        hint = Gtk.Label(
            label="Cable emulation patches Rocksmith at launch to accept your guitar input. Use RealTone Cable in the game and calibrate; if detection fails, try Direct Connect."
        )
        hint.get_style_context().add_class("ncl-sub")
        hint.set_xalign(0)
        hint.set_line_wrap(True)
        outer.pack_start(hint, False, False, 0)

        outer.pack_start(Gtk.Separator(), False, False, 4)
        outer.pack_start(self._player_box(1), False, False, 0)
        outer.pack_start(self._output_box(), False, False, 0)

        self.multi_check = Gtk.CheckButton(label="Enable multiplayer (second input device)")
        self.multi_check.connect("toggled", self._on_multi)
        outer.pack_start(self.multi_check, False, False, 0)
        self.player2_box = self._player_box(2)
        outer.pack_start(self.player2_box, False, False, 0)

        advanced = Gtk.Expander(label="Advanced offsets and Proton audio")
        adv = Gtk.Grid(column_spacing=8, row_spacing=6)
        adv.set_border_width(8)
        self.manual_off_check = Gtk.CheckButton(label="Manual memory offsets")
        self.offset_vid = Gtk.Entry()
        self.offset_pid = Gtk.Entry()
        self.latency_entry = Gtk.Entry()
        self.gamemode_check = Gtk.CheckButton(label="Use gamemoderun if installed")
        self.ini_check = Gtk.CheckButton(label="Apply Proton-safe Rocksmith.ini audio tweaks")
        self.audio_combo = Gtk.ComboBoxText()
        self.audio_combo.append("alsa", "Wine audio: ALSA through Pulse/PipeWire")
        self.audio_combo.append("pulse", "Wine audio: Pulse/PipeWire")
        self.audio_combo.append("default", "Wine audio: leave unchanged")
        self.runtime_check = Gtk.CheckButton(
            label="Wrap in Steam Linux Runtime (blocks the cable patch — leave off)"
        )
        self.zero_ids_check = Gtk.CheckButton(
            label="Use default-input IDs (0000:0000) for Pulse audio routing"
        )
        adv.attach(self.manual_off_check, 0, 0, 2, 1)
        adv.attach(Gtk.Label(label="VID offset", xalign=1), 0, 1, 1, 1)
        adv.attach(self.offset_vid, 1, 1, 1, 1)
        adv.attach(Gtk.Label(label="PID offset", xalign=1), 0, 2, 1, 1)
        adv.attach(self.offset_pid, 1, 2, 1, 1)
        adv.attach(Gtk.Label(label="PIPEWIRE_LATENCY", xalign=1), 0, 3, 1, 1)
        adv.attach(self.latency_entry, 1, 3, 1, 1)
        adv.attach(self.gamemode_check, 0, 4, 2, 1)
        adv.attach(self.ini_check, 0, 5, 2, 1)
        adv.attach(self.audio_combo, 0, 6, 2, 1)
        adv.attach(self.runtime_check, 0, 7, 2, 1)
        adv.attach(self.zero_ids_check, 0, 9, 2, 1)
        advanced.add(adv)
        outer.pack_start(advanced, False, False, 0)

        buttons = Gtk.Box(spacing=8)
        self.refresh_btn = Gtk.Button(label="Refresh devices")
        self.refresh_btn.connect("clicked", lambda *_: self.refresh_devices())
        self.save_btn = Gtk.Button(label="Save settings")
        self.save_btn.connect("clicked", lambda *_: self.save_from_widgets(notice=True))
        self.p2_btn = Gtk.Button(label="Activate Player 2 (Ctrl+M)")
        self.p2_btn.set_sensitive(False)
        self.p2_btn.connect("clicked", lambda *_: self._activate_player2())
        self.patch_now_btn = Gtk.Button(label="Patch running game")
        self.patch_now_btn.connect("clicked", lambda *_: self._patch_running())
        self.launch_btn = Gtk.Button(label="Launch Rocksmith")
        self.launch_btn.get_style_context().add_class("suggested-action")
        self.launch_btn.connect("clicked", lambda *_: self.launch())
        buttons.pack_start(self.refresh_btn, False, False, 0)
        buttons.pack_start(self.save_btn, False, False, 0)
        buttons.pack_end(self.launch_btn, False, False, 0)
        buttons.pack_end(self.p2_btn, False, False, 0)
        buttons.pack_end(self.patch_now_btn, False, False, 0)
        outer.pack_start(buttons, False, False, 4)

        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(160)
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_view.get_style_context().add_class("ncl-log")
        self.log_view.modify_font(Pango.FontDescription("Monospace 10"))
        scroll.add(self.log_view)
        outer.pack_start(scroll, True, True, 0)

        self.log(
            f"{APP_NAME} {__version__} — Linux rewrite of Maxx53/NoCableLauncher. "
            f"Steam app {STEAM_APP_ID}."
        )

    def _path_row(self, grid, row, label, entry, browse) -> int:
        grid.attach(Gtk.Label(label=label, xalign=1), 0, row, 1, 1)
        grid.attach(entry, 1, row, 1, 1)
        entry.set_hexpand(True)
        if browse:
            btn = Gtk.Button(label="Browse")
            btn.connect("clicked", browse)
            grid.attach(btn, 2, row, 1, 1)
        return row + 1

    def _apply_theme(self) -> None:
        dark = self.settings.theme != "light"
        self.settings.theme = "dark" if dark else "light"
        self.theme_provider.load_from_data((DARK_CSS if dark else LIGHT_CSS).encode("utf-8"))
        self.theme_button.set_label("Light mode" if dark else "Dark mode")
        self.theme_button.set_tooltip_text("Switch to light mode" if dark else "Switch to dark mode")
        for combo in (self.p1_combo, self.p2_combo, self.output_combo):
            self._style_audio_combo(combo)

    def _toggle_theme(self, *_args) -> None:
        self.settings.theme = "light" if self.settings.theme == "dark" else "dark"
        self._apply_theme()
        try:
            self.settings.save()
        except OSError as exc:
            self.log(f"Could not save theme preference: {exc}")

    def _style_audio_combo(self, combo: Gtk.ComboBoxText) -> None:
        combo.get_style_context().add_class("audio-device")
        # ComboBoxText draws device names with CellRendererText, not Gtk.Label.
        # Set the renderer too so popup rows do not inherit pale theme text.
        for renderer in combo.get_cells():
            if isinstance(renderer, Gtk.CellRendererText):
                renderer.set_property("foreground", "#080808" if self.settings.theme == "light" else "#ffffff")
                renderer.set_property("foreground-set", True)
                renderer.set_property("weight", Pango.Weight.SEMIBOLD)

    def _player_box(self, player: int) -> Gtk.Frame:
        frame = Gtk.Frame(label=f"Player {player} input")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_border_width(8)
        combo = Gtk.ComboBoxText()
        self._style_audio_combo(combo)
        manual = Gtk.CheckButton(label="Manual VID / PID")
        id_box = Gtk.Box(spacing=8)
        vid = Gtk.Entry()
        pid = Gtk.Entry()
        vid.set_width_chars(6)
        pid.set_width_chars(6)
        id_box.pack_start(Gtk.Label(label="VID"), False, False, 0)
        id_box.pack_start(vid, False, False, 0)
        id_box.pack_start(Gtk.Label(label="PID"), False, False, 0)
        id_box.pack_start(pid, False, False, 0)
        box.pack_start(combo, False, False, 0)
        box.pack_start(manual, False, False, 0)
        box.pack_start(id_box, False, False, 0)
        frame.add(box)
        setattr(self, f"p{player}_combo", combo)
        setattr(self, f"p{player}_manual", manual)
        setattr(self, f"p{player}_vid", vid)
        setattr(self, f"p{player}_pid", pid)
        combo.connect("changed", lambda *_: self._combo_to_ids(player))
        manual.connect("toggled", lambda *_: self._manual_toggled(player))
        return frame

    def _output_box(self) -> Gtk.Frame:
        frame = Gtk.Frame(label="Game output")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_border_width(8)
        row = Gtk.Box(spacing=8)
        self.output_combo = Gtk.ComboBoxText()
        self._style_audio_combo(self.output_combo)
        self.output_combo.set_hexpand(True)
        self.set_output_btn = Gtk.Button(label="Apply input and output")
        self.set_output_btn.connect("clicked", lambda *_: self.apply_audio_output())
        row.pack_start(self.output_combo, True, True, 0)
        row.pack_start(self.set_output_btn, False, False, 0)
        hint = Gtk.Label(
            label="Choose headphones, speakers, or your interface’s playback output. Apply also switches active Rocksmith Pulse/PipeWire streams and changes system defaults."
        )
        hint.get_style_context().add_class("ncl-sub")
        hint.set_xalign(0)
        hint.set_line_wrap(True)
        box.pack_start(row, False, False, 0)
        box.pack_start(hint, False, False, 0)
        frame.add(box)
        return frame

    def _on_multi(self, *_args) -> None:
        self.player2_box.set_sensitive(self.multi_check.get_active())

    def _manual_toggled(self, player: int) -> None:
        manual = getattr(self, f"p{player}_manual").get_active()
        getattr(self, f"p{player}_combo").set_sensitive(not manual)
        getattr(self, f"p{player}_vid").set_editable(manual)
        getattr(self, f"p{player}_pid").set_editable(manual)

    def _combo_to_ids(self, player: int) -> None:
        if getattr(self, f"p{player}_manual").get_active():
            return
        combo: Gtk.ComboBoxText = getattr(self, f"p{player}_combo")
        ident = combo.get_active_id()
        device = device_by_guid(self.devices, ident) if ident else None
        if device:
            getattr(self, f"p{player}_vid").set_text(device.vid)
            getattr(self, f"p{player}_pid").set_text(device.pid)

    def _browse_game(self, *_args) -> None:
        self._browse_into(self.game_entry, "Select Rocksmith2014.exe")

    def _browse_proton(self, *_args) -> None:
        self._browse_into(self.proton_entry, "Select the Proton launcher script")

    def _browse_into(self, entry: Gtk.Entry, title: str) -> None:
        dialog = Gtk.FileChooserDialog(
            title=title, parent=self, action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        if dialog.run() == Gtk.ResponseType.OK:
            entry.set_text(dialog.get_filename() or "")
        dialog.destroy()

    def autodetect(self) -> None:
        steam = find_steam_root()
        if steam and not self.settings.steam_root:
            self.settings.steam_root = str(steam)
        exe = find_game_exe(steam, self.settings.game_path)
        if exe:
            self.settings.game_path = str(exe)
            xml = exe.parent / "Settings.xml"
            if xml.exists() and not config_path().exists():
                merge_imported(self.settings, import_windows_settings(xml))
                self.settings.game_path = str(exe)
                self.log(f"Imported Windows settings from {xml}")
        proton = find_proton(steam, self.settings.proton_path)
        if proton:
            self.settings.proton_path = str(proton)
        prefix = find_prefix(steam)
        if prefix:
            self.settings.prefix_path = str(prefix)
        if self.settings.game_path.startswith("C:") or self.settings.game_path.endswith("steam://rungameid/221680"):
            if exe:
                self.settings.game_path = str(exe)

    def refresh_devices(self) -> None:
        self.devices = list_capture_devices()
        self.outputs = list_playback_devices()
        current_out = self.output_combo.get_active_id()
        self.output_combo.remove_all()
        for device in self.outputs:
            self.output_combo.append(device.guid, device.output_label)
        want_out = current_out or self.settings.output_guid
        out = device_by_guid(self.outputs, want_out) if want_out else None
        if out is None and want_out:
            self.output_combo.append(want_out, "Unavailable output — reconnect or choose another")
            self.output_combo.set_active_id(want_out)
        if out is None and not want_out:
            out = preferred_output(self.outputs)
        if out:
            self.output_combo.set_active_id(out.guid)
        elif self.outputs and not want_out:
            self.output_combo.set_active(0)
        for player in (1, 2):
            combo: Gtk.ComboBoxText = getattr(self, f"p{player}_combo")
            current = combo.get_active_id()
            combo.remove_all()
            for device in self.devices:
                combo.append(device.guid, device.label)
            want = current or (self.settings.guid1 if player == 1 else self.settings.guid2)
            chosen = device_by_guid(self.devices, want) if want else None
            if chosen is None and want:
                combo.append(want, "Unavailable input — reconnect or choose another")
                combo.set_active_id(want)
                continue
            if chosen is None and player == 1:
                chosen = preferred_capture(self.devices)
            if chosen:
                combo.set_active_id(chosen.guid)
            elif self.devices:
                combo.set_active(0)
        if not self.devices:
            self.log("No capture devices found. Plug in an interface and click Refresh.")
        else:
            self.log(f"Found {len(self.devices)} capture device(s).")
        if self.outputs:
            chosen = device_by_guid(self.outputs, self.output_combo.get_active_id() or "")
            label = chosen.output_label if chosen else f"{len(self.outputs)} playback device(s)"
            self.log(f"Game output: {label}")
        else:
            self.log("No playback devices found.")

    def _load_into_widgets(self) -> None:
        s = self.settings
        self.game_entry.set_text(s.game_path)
        self.proton_entry.set_text(s.proton_path)
        self.steam_entry.set_text(s.steam_root)
        self.prefix_entry.set_text(s.prefix_path)
        self.method_combo.set_active_id(s.launch_method)
        self.p1_vid.set_text(s.vid)
        self.p1_pid.set_text(s.pid)
        self.p2_vid.set_text(s.vid2)
        self.p2_pid.set_text(s.pid2)
        self.p1_manual.set_active(s.manual_dev1)
        self.p2_manual.set_active(s.manual_dev2)
        self.multi_check.set_active(s.multiplayer)
        self.player2_box.set_sensitive(s.multiplayer)
        self.manual_off_check.set_active(s.manual_offsets)
        self.offset_vid.set_text(s.offset_vid)
        self.offset_pid.set_text(s.offset_pid)
        self.latency_entry.set_text(s.pipewire_latency)
        self.gamemode_check.set_active(s.use_gamemode)
        self.ini_check.set_active(s.apply_ini_tweaks)
        self.audio_combo.set_active_id(s.wine_audio_driver)
        self.runtime_check.set_active(s.use_steam_runtime)
        self.dc_check.set_active(s.apply_direct_connect)
        self.memory_check.set_active(s.apply_memory_patch)
        self.zero_ids_check.set_active(s.proton_zero_ids)
        self._manual_toggled(1)
        self._manual_toggled(2)
        self._select_player_device(1, s.guid1, s.vid, s.pid)
        self._select_player_device(2, s.guid2, s.vid2, s.pid2)
        if s.output_guid and device_by_guid(self.outputs, s.output_guid):
            self.output_combo.set_active_id(s.output_guid)

    def _select_player_device(self, player: int, guid: str, vid: str, pid: str) -> None:
        combo: Gtk.ComboBoxText = getattr(self, f"p{player}_combo")
        if guid:
            existing = device_by_guid(self.devices, guid)
            if existing or combo.get_active_id() == guid:
                combo.set_active_id(guid)
                return
        best = preferred_capture(self.devices)
        if best and player == 1:
            combo.set_active_id(best.guid)
            return
        vid, pid = (vid or "").upper(), (pid or "").upper()
        if vid and vid != "0000":
            for device in self.devices:
                if device.vid == vid and device.pid == pid:
                    combo.set_active_id(device.guid)
                    return

    def save_from_widgets(self, notice: bool = False) -> Settings:
        s = self.settings
        s.game_path = self.game_entry.get_text().strip()
        s.proton_path = self.proton_entry.get_text().strip()
        s.steam_root = self.steam_entry.get_text().strip()
        s.prefix_path = self.prefix_entry.get_text().strip()
        s.launch_method = self.method_combo.get_active_id() or "proton"
        s.is_steam = s.launch_method == "steam"
        s.vid = self.p1_vid.get_text().strip().upper() or "0000"
        s.pid = self.p1_pid.get_text().strip().upper() or "0000"
        s.vid2 = self.p2_vid.get_text().strip().upper() or "0000"
        s.pid2 = self.p2_pid.get_text().strip().upper() or "0000"
        s.guid1 = self.p1_combo.get_active_id() or ""
        s.guid2 = self.p2_combo.get_active_id() or ""
        s.manual_dev1 = self.p1_manual.get_active()
        s.manual_dev2 = self.p2_manual.get_active()
        s.multiplayer = self.multi_check.get_active()
        s.offset_vid = self.offset_vid.get_text().strip() or s.offset_vid
        s.offset_pid = self.offset_pid.get_text().strip() or s.offset_pid
        s.manual_offsets = self.manual_off_check.get_active()
        s.pipewire_latency = self.latency_entry.get_text().strip()
        s.use_gamemode = self.gamemode_check.get_active()
        s.apply_ini_tweaks = self.ini_check.get_active()
        s.wine_audio_driver = self.audio_combo.get_active_id() or "alsa"
        s.use_steam_runtime = self.runtime_check.get_active()
        s.apply_direct_connect = self.dc_check.get_active()
        s.apply_memory_patch = self.memory_check.get_active()
        s.proton_zero_ids = self.zero_ids_check.get_active()
        s.output_guid = self.output_combo.get_active_id() or ""
        s.save()
        if notice:
            self.log(f"Saved {config_path()}")
        return s

    def log(self, message: str) -> None:
        try:
            with (config_dir() / "launcher.log").open("a", encoding="utf-8") as handle:
                handle.write(f"{datetime.now().isoformat(timespec='seconds')} {message.rstrip()}\n")
        except OSError:
            pass

        def append() -> None:
            buf = self.log_view.get_buffer()
            end = buf.get_end_iter()
            buf.insert(end, message.rstrip() + "\n")
            self.log_view.scroll_to_iter(buf.get_end_iter(), 0.0, False, 0, 0)

        if threading.current_thread() is threading.main_thread():
            append()
        else:
            GLib.idle_add(append)

    def launch(self) -> None:
        if self._launching:
            return
        settings = self.save_from_widgets()
        if not Path(settings.game_path).is_file():
            self.log(f"Game exe not found: {settings.game_path}")
            return
        if settings.launch_method != "steam" and not Path(settings.proton_path).is_file():
            self.log(f"Proton not found: {settings.proton_path}")
            return
        if settings.multiplayer and settings.vid == settings.vid2 and settings.pid == settings.pid2:
            self.log("Player 1 and Player 2 use the same VID/PID — multiplayer will not work.")
            return
        self._launching = True
        self.launch_btn.set_sensitive(False)
        self.set_output_btn.set_sensitive(False)
        threading.Thread(target=self._launch_worker, args=(settings,), daemon=True).start()

    def apply_audio_output(self) -> None:
        if self._launching:
            self.log("Wait for launch preparation to finish before changing audio.")
            return
        settings = self.save_from_widgets()
        if not settings.guid1 or not settings.output_guid:
            self.log("Select both a guitar input and game output, then apply.")
            return
        try:
            self._prepare_input(settings)
            inputs, outputs, errors = move_game_streams(
                getattr(settings, "pulse_source", ""), getattr(settings, "pulse_sink", "")
            )
            self.log(f"Audio selection saved. Switched {inputs} input and {outputs} output game stream(s).")
            for error in errors:
                self.log(error)
            self.log("If either stream was not switched, restart Rocksmith. Calibrate after changing guitar input.")
        except (OSError, LaunchError) as exc:
            self.log(f"Audio routing failed: {exc}")

    def _apply_output_routing(
        self, settings: Settings, device: CaptureDevice | None = None
    ) -> CaptureDevice | None:
        output = device_by_guid(getattr(self, "outputs", []), settings.output_guid)
        if output is None and settings.output_guid:
            raise LaunchError("Selected output is unavailable. Refresh devices and select an output.")
        if output is None:
            output = preferred_output(
                getattr(self, "outputs", []) or list_playback_devices() or list_alsa_playback_devices()
            )
        settings.pulse_sink = ""
        if output:
            settings.output_guid = output.guid
            settings.alsa_playback = output.alsa_id
            sink = pulse_sink_name(output)
            if not sink:
                raise LaunchError("Cannot resolve the selected output. Choose its PipeWire entry or check its audio profile.")
            if sink:
                settings.pulse_sink = sink
            if set_default_sink(output):
                self.log(f"Set system default playback to {output.name}")
        capture_id = device.alsa_id if device else ""
        playback_id = output.alsa_id if output else ""
        use_hw = settings.wine_audio_driver != "pulse"
        asound = write_pulse_asoundrc()
        if set_alsa_cards(
            settings.prefix_path,
            capture_card=capture_id,
            playback_card=playback_id,
            exclusive_hw=use_hw,
            default_capture_only=settings.apply_memory_patch and settings.proton_zero_ids,
        ):
            self.log(
                f"Wine audio via Pulse/PipeWire ({asound.name}): "
                f"playback={playback_id or 'selected server output'}  capture={capture_id or 'guitar'}"
            )
        if settings.apply_memory_patch and settings.proton_zero_ids:
            self.log("Cable emulation clears input aliases and launches with a private ALSA namespace containing only the selected default input/output.")
        settings.save()
        return output

    def _prepare_input(self, settings: Settings) -> tuple[str, str]:
        device = device_by_guid(self.devices, settings.guid1)
        settings.pulse_source = ""
        if device is None and not settings.manual_dev1:
            raise LaunchError("Selected input is unavailable. Refresh devices and select your guitar input.")
        patch_vid, patch_pid = settings.vid, settings.pid
        if settings.proton_zero_ids:
            patch_vid, patch_pid = "0000", "0000"
            self.log(
                "Cable patch target: 0000:0000 for the Pulse-backed default capture endpoint."
            )
        if device:
            source = pulse_source_name(device)
            if not source:
                raise LaunchError("Cannot resolve the selected input. Choose its PipeWire entry or check its audio profile.")
            if source:
                settings.pulse_source = source
            if set_default_source(device):
                self.log(f"Set system default capture to {device.name}")
            if set_device_mute(device, False):
                self.log(f"Unmuted player 1 capture ({device.name})")
        self._apply_output_routing(settings, device=device)
        return patch_vid, patch_pid

    def _launch_worker(self, settings: Settings) -> None:
        try:
            leftover = find_game_pids()
            if leftover:
                self.log(
                    "Stopping leftover Rocksmith so the cable patch can attach: "
                    + ", ".join(str(pid) for pid in leftover)
                )
                stop_stale_game()
            if settings.apply_ini_tweaks:
                output = device_by_guid(self.outputs, settings.output_guid) or preferred_output(
                    self.outputs
                )
                path = apply_audio_tweaks(
                    settings.game_path,
                    playback_device=output.alsa_id if output else "",
                )
                self.log(f"Updated audio settings in {path}")
            if settings.wine_audio_driver and settings.wine_audio_driver != "default":
                if set_prefix_audio_driver(settings.prefix_path, settings.wine_audio_driver):
                    self.log(f"Set Proton Wine audio driver to {settings.wine_audio_driver}")
            patch_vid, patch_pid = self._prepare_input(settings)
            if settings.apply_direct_connect:
                try:
                    dc = apply_direct_connect(Path(settings.game_path).parent, log=self.log)
                    if dc is False:
                        settings.apply_memory_patch = True
                except DirectConnectError as exc:
                    self.log(f"Direct Connect skipped ({exc}). Using the memory spoof instead.")
                    settings.apply_memory_patch = True
            if settings.multiplayer:
                set_source_mute(settings.guid2, True)
                self.log("Muted player 2 capture source until Ctrl+M / Activate Player 2.")
            if settings.launch_method == "steam":
                self.log("Steam is often already running with no window. Using Proton directly.")
                settings.launch_method = "proton"
            ignore_pids = set(find_game_pids())
            self.log("Starting Rocksmith via Proton...")
            launch_game(settings, log=self.log)
            if settings.apply_memory_patch:
                pid, off_v, off_p = patch_game(
                    patch_vid,
                    patch_pid,
                    settings.offset_vid,
                    settings.offset_pid,
                    manual_offsets=settings.manual_offsets,
                    ignore_pids=ignore_pids,
                    log=self.log,
                )
                self._game_pid = pid
                settings.offset_vid = off_v
                settings.offset_pid = off_p
                settings.save()
                GLib.idle_add(self.offset_vid.set_text, off_v)
                GLib.idle_add(self.offset_pid.set_text, off_p)
                if settings.multiplayer:
                    GLib.idle_add(self.p2_btn.set_sensitive, True)
                    self._stop_hotkey.clear()
                    self._hotkey_thread = start_ctrl_m_listener(self._activate_player2, self._stop_hotkey)
                    self.log("Player 1 patched. Press Ctrl+M in-game or click Activate Player 2.")
                else:
                    self.log(
                    "Cable ID patch verified. Select RealTone Cable in Path / Input, then calibrate. "
                    "This verifies the patch bytes; game-side input detection still needs to succeed."
                )
            else:
                self.log("Game started. In Rocksmith: Path / Input → Direct Connect, then pick your interface.")
        except (PatchError, LaunchError, DirectConnectError, OSError) as exc:
            self.log(f"Error: {exc}")
        finally:
            self._launching = False
            GLib.idle_add(self.launch_btn.set_sensitive, True)
            GLib.idle_add(self.set_output_btn.set_sensitive, True)

    def _patch_running(self) -> None:
        settings = self.save_from_widgets()
        settings.apply_memory_patch = True

        def work() -> None:
            try:
                patch_vid, patch_pid = self._prepare_input(settings)
                pid, off_v, off_p = patch_game(
                    patch_vid,
                    patch_pid,
                    settings.offset_vid,
                    settings.offset_pid,
                    manual_offsets=settings.manual_offsets,
                    log=self.log,
                )
                self._game_pid = pid
                settings.offset_vid = off_v
                settings.offset_pid = off_p
                settings.save()
                GLib.idle_add(self.offset_vid.set_text, off_v)
                GLib.idle_add(self.offset_pid.set_text, off_p)
                self.log("Patched the running game. If it already showed no cable, restart Rocksmith.")
            except (PatchError, LaunchError, OSError) as exc:
                self.log(f"Error: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def _activate_player2(self) -> None:
        settings = self.settings
        if not settings.multiplayer or self._game_pid is None:
            return
        if not process_running(self._game_pid):
            self.log("Game is no longer running.")
            return

        def work() -> None:
            try:
                set_source_mute(settings.guid2, False)
                patch_game(
                    settings.vid2,
                    settings.pid2,
                    settings.offset_vid,
                    settings.offset_pid,
                    manual_offsets=True,
                    game_pid=self._game_pid,
                    log=self.log,
                )
                self.log("Player 2 patched. Select Multiplayer in the game menu.")
                GLib.idle_add(self.p2_btn.set_sensitive, False)
            except PatchError as exc:
                self.log(f"Player 2 patch failed: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def _on_destroy(self, *_args) -> None:
        self._stop_hotkey.set()
        try:
            self.save_from_widgets()
        except OSError:
            pass
        if Gtk.main_level() > 0:
            Gtk.main_quit()

    def run_wrap(self, argv: list[str]) -> int:
        settings = self.save_from_widgets()
        self.log("Wrap mode: launching Steam/Proton command, then patching.")
        if settings.apply_ini_tweaks and Path(settings.game_path).is_file():
            apply_audio_tweaks(settings.game_path)
        if settings.apply_direct_connect and Path(settings.game_path).is_file():
            try:
                apply_direct_connect(Path(settings.game_path).parent, log=self.log)
            except DirectConnectError as exc:
                self.log(f"Direct Connect skipped ({exc}).")
        self._prepare_input(settings)
        wrap_and_exec(argv, settings)
        if settings.apply_memory_patch:
            threading.Thread(target=self._wrap_patch, args=(settings,), daemon=True).start()
        return 0

    def _wrap_patch(self, settings: Settings) -> None:
        try:
            patch_vid = "0000" if settings.proton_zero_ids else settings.vid
            patch_pid = "0000" if settings.proton_zero_ids else settings.pid
            pid, off_v, off_p = patch_game(
                patch_vid,
                patch_pid,
                settings.offset_vid,
                settings.offset_pid,
                manual_offsets=settings.manual_offsets,
                log=self.log,
            )
            self._game_pid = pid
            settings.offset_vid = off_v
            settings.offset_pid = off_p
            settings.save()
            if settings.multiplayer:
                GLib.idle_add(self.p2_btn.set_sensitive, True)
        except PatchError as exc:
            self.log(f"Error: {exc}")


def run_gui(settings: Settings, wrap_argv: list[str] | None = None) -> int:
    Gtk.init([])
    win = LauncherWindow(settings)
    win.show_all()
    if wrap_argv:
        win.run_wrap(wrap_argv)
    Gtk.main()
    return 0
