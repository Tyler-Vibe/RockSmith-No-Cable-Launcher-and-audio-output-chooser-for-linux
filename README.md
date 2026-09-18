# RockSmith No Cable Launcher and audio output chooser for linux

A Python/GTK launcher for **Rocksmith 2014** through Steam Proton. Select a guitar input and a separate headphone/speaker output, then launch with game-side RealTone cable emulation.

**Experimental:** game builds and Proton audio behavior vary. This project does not create a physical USB RealTone device. A verified memory patch confirms the write succeeded; it does not by itself confirm calibration or note detection. Rocksmith+, native Windows, and macOS are not supported targets.

## Vibe-coded, community-tested

This app was **vibe coded** with AI assistance. It is experimental, and bugs are expected. If something breaks or behaves unexpectedly, please [report a bug](https://github.com/Tyler-Vibe/RockSmith-No-Cable-Launcher-and-audio-output-chooser-for-linux/issues/new) so we can investigate and fix it together. Include your Linux distribution, Proton version, audio interface, steps to reproduce, and relevant logs (remove personal information first).

## Features

- Separate input and output menus, including individual PipeWire endpoints.
- Saved device choices and an Apply input and output action.
- RealTone ID patching restricted to the game's executable image, with read-back verification.
- A private ALSA configuration for default-input emulation, keeping extra capture devices out of the game's audio namespace.
- Automatic Steam/game/Proton discovery and editable paths.
- Dark mode by default, a light/dark button, and high-contrast audio lists.
- Optional Direct Connect menu patch for compatible game archives.

## Installation (Ubuntu / Debian)

These instructions assume a native Steam installation and a desktop session using PipeWire with its PulseAudio compatibility service. Flatpak Steam, other distributions, and alternate audio stacks need additional setup and are not covered by the tested configuration.

### 1. Prepare Steam and the game

1. Install Steam and your own copy of Rocksmith 2014.
2. In Steam, open **Rocksmith → Properties → Compatibility** and select **Proton Experimental**. Let Steam download it.
3. Launch the game once from Steam, then exit it. This creates its Proton prefix.
4. Connect your guitar interface and headphones/speakers. In your desktop sound settings, confirm that the input reacts when you play.

The launcher includes no game files, Steam credentials, or proprietary game binaries.

### 2. Install dependencies

```bash
sudo apt update
sudo apt install git python3 python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
  python3-cryptography python3-py7zr pipewire-bin wireplumber \
  pulseaudio-utils libasound2-plugins
```

Package names can vary by distribution release. `pw-dump` enumerates devices, `wpctl` manages defaults, and `pactl` enables live stream switching. The ALSA Pulse plugin provides the game's audio bridge. Installing these utilities does not automatically configure a different desktop sound server; check your existing desktop audio first.

Some older Proton builds require 32-bit audio libraries. On an Ubuntu/Debian amd64 installation that needs them:

```bash
sudo dpkg --add-architecture i386
sudo apt update
sudo apt install libasound2-plugins:i386
```

Use the system Python so it can find distribution-installed GTK bindings. A plain virtual environment often cannot import `gi`.

Check the prerequisites:

```bash
python3 -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk; import cryptography, py7zr; print("Python dependencies OK")'
pw-dump --version
wpctl status
pactl info
```

### 3. Download and start

```bash
git clone https://github.com/Tyler-Vibe/RockSmith-No-Cable-Launcher-and-audio-output-chooser-for-linux.git
cd RockSmith-No-Cable-Launcher-and-audio-output-chooser-for-linux
chmod +x nocable-launcher
./nocable-launcher
```

Alternatively, from the project directory:

```bash
python3 -m nocablelauncher
```

No compilation, administrator privileges, or `pip install` is needed to run the launcher after the system dependencies are installed. Do not run the launcher as root.

## First-time setup

Confirm the detected paths at the top of the window. If a field is empty, use Browse or enter the path manually.

| Field | What to select |
| --- | --- |
| Game exe | `Rocksmith2014.exe` inside the game's installation folder |
| Proton | The `proton` script in `steamapps/common/Proton - Experimental/` |
| Steam root | Your Steam installation directory, commonly `~/.local/share/Steam` or `~/.steam/steam` |
| Proton prefix | The game's `steamapps/compatdata/221680` directory, **not** its inner `pfx` directory |

Steam's **Properties → Installed Files → Browse** locates the game. Libraries on another drive have their own `steamapps` directory. The app ID is `221680`.

### Choose devices and play

1. Click **Refresh devices** after connecting your interface.
2. Set **Player 1 input** to the device receiving your guitar. Do not select your webcam or headset microphone unless that is intentionally your input.
3. Set **Game output** to your headphones, speakers, or interface playback output. Input and output may be different devices.
4. Leave **Emulate RealTone cable using the selected input** enabled.
5. In Advanced, start with **Wine audio: ALSA through Pulse/PipeWire**, **Use default-input IDs (0000:0000)**, and the Proton-safe INI tweaks enabled. Leave manual offsets and Steam Linux Runtime wrapping off.
6. Click **Launch Rocksmith** once. The launcher saves and applies both device selections automatically. It may stop an existing Rocksmith session before starting a fresh one.
7. Wait for the log to report a verified cable-table patch, then select **RealTone Cable** in the game's input settings and calibrate.
8. Play a few notes and confirm the game responds. Adjust your physical interface gain if necessary.

Keep the launcher open while playing. Changing selection alone does not move an already-running stream: click **Apply input and output**, or relaunch. Live switching requires `pactl`, an identifiable Rocksmith stream, and a compatible audio path; a restart may still be necessary. Recalibrate after changing guitar inputs.

The **Light mode / Dark mode** button switches immediately and remembers your preference. Existing settings without a theme preference start in dark mode.

## How cable emulation works

The launcher waits for the game to unpack, finds its RealTone USB-ID table inside the PE image, and substitutes IDs for the desired audio path. Default-input emulation uses `0000:0000` and routes that endpoint through `PULSE_SOURCE` and `PULSE_SINK`.

For the ALSA default-input mode, `ALSA_CONFIG_PATH` points only the game process at `game-audio.conf`. This configuration exposes a single default input/output via Pulse instead of enumerating all physical ALSA cards. Clearing additional input aliases also prevents duplicate virtual inputs. Physical microphones remain available to other desktop applications.

The launcher retains detached Wine children so it can patch the game under normal Linux process-access restrictions. Do not disable system-wide ptrace protections to use it.

## Settings and files changed

Settings and diagnostics normally live in `~/.config/nocablelauncher/` (or `$XDG_CONFIG_HOME/nocablelauncher/`):

- `settings.json`: paths, device selections, theme, and options.
- `launcher.log`: routing, patch verification, and error messages.
- `launch.log`: Proton/Wine launch output.
- `steam-221680.log`: Proton diagnostics when produced.
- `game-audio.conf`: the private ALSA configuration.

Launching/applying audio can change the **desktop's default input and output**, unmute the selected input, and update audio settings in the game's Wine prefix. The app also writes `~/.config/alsa/asoundrc`; it creates or updates `~/.asoundrc` only when missing or marked as launcher-managed. Back up existing audio configuration before your first run if you customize ALSA yourself.

The INI option modifies `Rocksmith.ini`, including `ExclusiveMode=0`, `Win32UltraLowLatencyMode=0`, `EnableMicrophone=1`, `RealToneCableOnly=0`, and clearing `ForceDefaultPlaybackDevice`. Back up this file if you want to preserve previous values.

## Optional Direct Connect

Enable **Direct Connect** only as an alternative for a compatible game build. It edits menu data in `cache.psarc` and creates `cache.psarc.bak` on first apply. Once available, choose **Path / Input → Direct Connect** in the game.

Older 2014 menu archives may lack this screen. The launcher reports that and falls back to the memory patch. Direct Connect is not guaranteed merely because the checkbox is enabled.

## Advanced and experimental options

- **Latency:** the initial `PIPEWIRE_LATENCY` is `256/48000`. Lower values may reduce latency but can cause crackling; increase it if audio breaks up.
- **GameMode:** optional and off by default. Enable only if `gamemoderun` is installed.
- **Steam launch wrapper:** `"/absolute/path/to/nocable-launcher" --wrap -- %command%` is available as an advanced Steam launch option. Steam runtime containers may block memory access. Prefer launching directly through this application for initial setup.
- **Launch method:** the UI currently redirects its Steam-client choice to direct Proton for patching. Direct Proton can fall back to Steam after an early failure; that fallback may not be patchable. Check the log.
- **Manual USB IDs / offsets:** for experienced users and compatible builds. Leave them at their automatic/default values initially.
- **Multiplayer:** experimental and not verified with the isolated single-input configuration. Do not enable it for the standard one-guitar setup. The legacy player-2 controls are not a guarantee that two interfaces will work.
- **Other audio mods:** do not run the Windows NoCableLauncher simultaneously. RS_ASIO/WineASIO is a separate setup and should not be combined with this memory patch without understanding both configurations.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `No module named gi` | Use system `python3`; install GTK/PyGObject packages above; leave a plain venv. |
| No devices in a menu | Check `wpctl status`; reconnect the device; refresh; confirm PipeWire is running in your user session. |
| Saved device unavailable | Reconnect and refresh, or explicitly choose another device. |
| Two RealTone cables detected | Use ALSA default-input emulation, disable multiplayer and other cable mods, and relaunch through this app so the private audio namespace takes effect. Do not only patch an already-running game. |
| Cable unplugged | Read `launcher.log` for a verified patch; confirm your source is active and not muted; choose RealTone Cable and recalibrate. A patch success is not an audio-signal test. |
| No guitar signal | Verify the correct physical input and interface gain in desktop audio settings; check guitar volume/cable; select the corresponding source in the launcher. |
| No playback | Select the intended output; check system volume; confirm the ALSA Pulse plugin for your Proton architecture is installed. |
| Crackles or dropouts | Increase the latency buffer, keep exclusive mode off initially, and avoid overloaded audio devices. |
| Permission/ptrace error | Close the game and relaunch from this launcher. Avoid a separate Steam launch or the optional runtime wrapper during initial setup. |
| Signature not found or ambiguous | The game build may be unsupported. Do not guess addresses; include the log when reporting the issue. |
| Direct Connect unavailable | Some older game archives do not contain its menu. Use the cable patch instead. |

When reporting an issue, include distribution, Proton version, input/output models, launch method, and the relevant launcher log. Review logs before sharing: they can contain usernames and local paths.

## Updating

Exit the launcher, then run in the cloned directory:

```bash
git pull --ff-only
./nocable-launcher
```

Your saved settings are outside the checkout and remain intact.

## Removing or reverting

Exit the game and launcher. Remove the cloned directory when no longer needed. Settings can be removed separately from `~/.config/nocablelauncher/`.

To undo game/audio changes, restore your own `Rocksmith.ini` and ALSA backups, choose your preferred desktop sound defaults, and restore `cache.psarc.bak` to `cache.psarc` if Direct Connect was applied. Also remove `cache.psarc.directconnect` if restoring the original archive. Wine audio registry preferences remain in the game's prefix; change those through Wine configuration or restore a prefix backup. Removing the launcher alone does not reverse those settings.

## Development

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q nocablelauncher tests
```

Tests cover routing, settings, INI changes, image-scoped patching, and archive menu transformations. They do not replace a real calibration test. The public source distribution excludes local virtual environments, logs, game files, and bundled 7-Zip binaries; install `python3-py7zr` as above.

## Credits and license

Based on the approach and signature of [Maxx53/NoCableLauncher](https://github.com/Maxx53/NoCableLauncher), with historical inspiration from phobos2077's multiplayer script. This Linux port is maintained under [Tyler-Vibe](https://github.com/Tyler-Vibe).

Distributed under GPL-2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE). Rocksmith and RealTone names belong to their respective owners. This is an unofficial community project, not an Ubisoft product.
