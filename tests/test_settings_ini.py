import tempfile
import unittest
from pathlib import Path

from nocablelauncher.ini import apply_audio_tweaks
from nocablelauncher.settings import Settings, import_windows_settings


WINDOWS_XML = """<?xml version="1.0" encoding="utf-8"?>
<settings>
  <setting name="gamePath">C:\\Games\\Rocksmith2014.exe</setting>
  <setting name="isSteam">False</setting>
  <setting name="VID">0D8C</setting>
  <setting name="PID">0014</setting>
  <setting name="VID2">0000</setting>
  <setting name="PID2">0000</setting>
  <setting name="manualDev1">False</setting>
  <setting name="manualDev2">False</setting>
  <setting name="Multiplayer">False</setting>
  <setting name="offcetVID">012B7E0C</setting>
  <setting name="offcetPID">012B7E14</setting>
  <setting name="manualOffcets">False</setting>
  <setting name="GUID1">{0.0.1.00000000}.{abc}</setting>
  <setting name="GUID2"></setting>
</settings>
"""


class SettingsTests(unittest.TestCase):
    def test_import_windows_xml(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Settings.xml"
            path.write_text(WINDOWS_XML, encoding="utf-8")
            data = import_windows_settings(path)
        self.assertEqual(data["vid"], "0D8C")
        self.assertEqual(data["pid"], "0014")
        self.assertEqual(data["offset_vid"], "012B7E0C")
        self.assertFalse(data["is_steam"])
        self.assertEqual(data["launch_method"], "proton")

    def test_roundtrip_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            settings = Settings(
                vid="0D8C",
                pid="0014",
                game_path="/tmp/Rocksmith2014.exe",
                output_guid="alsa:card5",
            )
            settings.save(path)
            loaded = Settings.load(path)
        self.assertEqual(loaded.vid, "0D8C")
        self.assertEqual(loaded.pid, "0014")
        self.assertEqual(loaded.output_guid, "alsa:card5")


class IniTests(unittest.TestCase):
    def test_writes_proton_safe_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "Rocksmith2014.exe"
            exe.write_bytes(b"mz")
            ini = Path(tmp) / "Rocksmith.ini"
            ini.write_text("[Audio]\nExclusiveMode=1\nWin32UltraLowLatencyMode=1\n", encoding="utf-8")
            apply_audio_tweaks(exe)
            text = ini.read_text(encoding="utf-8")
        self.assertIn("ExclusiveMode=0", text)
        self.assertIn("Win32UltraLowLatencyMode=0", text)
        self.assertIn("EnableMicrophone=1", text)
        self.assertIn("RealToneCableOnly=0", text)
        self.assertIn("ForceDefaultPlaybackDevice=", text)


if __name__ == "__main__":
    unittest.main()
