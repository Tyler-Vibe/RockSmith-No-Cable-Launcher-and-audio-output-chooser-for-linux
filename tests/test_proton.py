import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from nocablelauncher.proton import build_env, proton_command
from nocablelauncher.settings import Settings


class ProtonCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        for target, value in (("nocablelauncher.proton.config_dir", Path(temporary.name)),
                              ("nocablelauncher.proton.write_pulse_asoundrc", Path(temporary.name) / "asoundrc"),
                              ("nocablelauncher.proton.find_steam_runtime", Path("/tmp/SteamLinuxRuntime/_v2-entry-point"))):
            mock = patch(target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)

    def test_wraps_runtime_and_points_at_exe(self):
        with TemporaryDirectory() as tmp:
            proton = Path(tmp) / "proton"
            proton.write_text("#!/bin/sh\n", encoding="utf-8")
            proton.chmod(0o755)
            exe = Path(tmp) / "Rocksmith2014.exe"
            exe.write_bytes(b"mz")
            settings = Settings(
                game_path=str(exe),
                proton_path=str(proton),
                steam_root="/example/steam",
                prefix_path="/tmp/pfx",
            )
            command = proton_command(settings)
            self.assertIn("waitforexitandrun", command)
            self.assertTrue(any("Rocksmith2014.exe" in part for part in command))
            self.assertFalse(any("SteamLinuxRuntime" in part or "_v2-entry-point" in part for part in command))
            settings.use_steam_runtime = True
            wrapped = proton_command(settings)
            self.assertTrue(any("SteamLinuxRuntime" in part or "_v2-entry-point" in part for part in wrapped))

    def test_env_has_compat_paths(self):
        settings = Settings(
            game_path="/example/steam/steamapps/common/Rocksmith2014/Rocksmith2014.exe",
            steam_root="/example/steam",
            prefix_path="/example/steam/steamapps/compatdata/221680",
        )
        env = build_env(settings)
        self.assertEqual(env["STEAM_COMPAT_APP_ID"], "221680")
        self.assertIn("steamapps", env["STEAM_COMPAT_LIBRARY_PATHS"])
        self.assertTrue(env["STEAM_COMPAT_INSTALL_PATH"].endswith("Rocksmith2014"))

    def test_env_routes_playback_to_headset(self):
        settings = Settings(
            game_path="/example/steam/steamapps/common/Rocksmith2014/Rocksmith2014.exe",
            steam_root="/example/steam",
            prefix_path="/example/steam/steamapps/compatdata/221680",
        )
        settings.alsa_playback = "A50"
        settings.pulse_sink = "alsa_output.usb-Logitech_A50"
        settings.pulse_source = "alsa_input.usb-C-Media"
        env = build_env(settings)
        self.assertNotIn("ALSA_CARD", env)
        self.assertEqual(env["PULSE_SINK"], "alsa_output.usb-Logitech_A50")
        self.assertEqual(env["PULSE_SOURCE"], "alsa_input.usb-C-Media")


if __name__ == "__main__":
    unittest.main()

class WineChildOwnershipTests(unittest.TestCase):
    def test_subreaper_keeps_orphaned_descendants_patchable(self):
        from nocablelauncher import proton
        with patch.object(proton.ctypes, 'CDLL') as loader:
            loader.return_value.prctl.return_value = 0
            proton.retain_wine_children()
            loader.return_value.prctl.assert_called_once_with(36, 1, 0, 0, 0)

    def test_subreaper_failure_stops_launch(self):
        from nocablelauncher import proton
        with patch.object(proton.ctypes, 'CDLL') as loader:
            loader.return_value.prctl.return_value = -1
            with self.assertRaises(proton.LaunchError):
                proton.retain_wine_children()

class SingleInputEnvironmentTests(unittest.TestCase):
    def test_cable_mode_isolates_alsa_to_game_process(self):
        import os
        with TemporaryDirectory() as tmp, \
             patch('nocablelauncher.proton.config_dir', return_value=Path(tmp)), \
             patch('nocablelauncher.proton.write_pulse_asoundrc') as global_config:
            settings = Settings()
            settings.pulse_source = 'selected-guitar'
            settings.pulse_sink = 'selected-headset'
            before = os.environ.get('ALSA_CONFIG_PATH')
            env = build_env(settings)
            self.assertEqual(env['PULSE_SOURCE'], 'selected-guitar')
            self.assertEqual(env['PULSE_SINK'], 'selected-headset')
            config = Path(env['ALSA_CONFIG_PATH']).read_text()
            self.assertIn('pcm.default', config)
            self.assertNotIn('ctl.hw', config)
            self.assertEqual(os.environ.get('ALSA_CONFIG_PATH'), before)
            global_config.assert_not_called()

    def test_no_isolation_when_memory_emulation_is_disabled(self):
        with TemporaryDirectory() as tmp, \
             patch('nocablelauncher.proton.config_dir', return_value=Path(tmp)), \
             patch('nocablelauncher.proton.write_pulse_asoundrc'), \
             patch.dict('os.environ', {}, clear=True):
            env = build_env(Settings(apply_memory_patch=False))
            self.assertNotIn('ALSA_CONFIG_PATH', env)
