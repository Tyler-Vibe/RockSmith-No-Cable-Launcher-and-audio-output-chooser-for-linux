import tempfile
import unittest
from pathlib import Path

from nocablelauncher.devices import CaptureDevice, is_likely_guitar_interface, preferred_output
from nocablelauncher.ini import apply_audio_tweaks
from nocablelauncher.wine_audio import _reg_multi_sz, set_alsa_cards


class PreferredOutputTests(unittest.TestCase):
    def test_prefers_a50_over_guitar_dongle(self):
        devices = [
            CaptureDevice("C-Media USB Audio Device", "0D8C", "0014", "alsa:card4", 4, "Device"),
            CaptureDevice("Logitech A50", "046D", "0B1C", "alsa:card5", 5, "A50"),
            CaptureDevice("HD-Audio Generic", "0000", "0000", "alsa:card0", 0, "Generic"),
        ]
        chosen = preferred_output(devices)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.alsa_id, "A50")
        self.assertIn("hw:A50", chosen.output_label)
        self.assertFalse(
            is_likely_guitar_interface(
                CaptureDevice("Logitech A50", "046D", "0B1C", "alsa:card5", 5, "A50")
            )
        )


class WineAlsaRoutingTests(unittest.TestCase):
    def test_writes_multi_sz_input_and_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            pfx = Path(tmp) / "pfx"
            pfx.mkdir()
            user = pfx / "user.reg"
            user.write_text(
                "WINE REGISTRY Version 2\n\n"
                "[Software\\\\Wine\\\\Drivers] 1\n"
                '"Audio"="alsa"\n',
                encoding="utf-8",
            )
            self.assertTrue(set_alsa_cards(tmp, capture_card="Device", playback_card="A50"))
            text = user.read_text(encoding="utf-8")
        self.assertIn(
            '"ALSAInputDevices"=' + _reg_multi_sz("pulse", "plughw:Device,0", "hw:Device"),
            text,
        )
        self.assertIn(
            '"ALSAOutputDevices"=' + _reg_multi_sz("pulse", "plughw:A50,0", "hw:A50"),
            text,
        )
        self.assertIn('"Device"="pulse"', text)
        self.assertNotIn("ALSACaptureDevices", text)
        self.assertNotIn('"Device"="hw:Device"', text)

    def test_multi_sz_is_utf16_hex(self):
        blob = _reg_multi_sz("hw:A50")
        self.assertTrue(blob.startswith("hex(7):"))
        self.assertIn("68,00,77,00,3a,00,41,00,35,00,30,00", blob)


class IniPlaybackTests(unittest.TestCase):
    def test_sets_force_default_playback(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "Rocksmith2014.exe"
            exe.write_bytes(b"mz")
            ini = Path(tmp) / "Rocksmith.ini"
            ini.write_text("[Audio]\nExclusiveMode=1\nForceDefaultPlaybackDevice=\n", encoding="utf-8")
            apply_audio_tweaks(exe, playback_device="A50")
            text = ini.read_text(encoding="utf-8")
        self.assertIn("ForceDefaultPlaybackDevice=", text)
        self.assertNotIn("ForceDefaultPlaybackDevice=A50", text)
        self.assertIn("ExclusiveMode=0", text)
