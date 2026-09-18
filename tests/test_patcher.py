import unittest

from nocablelauncher import CABLE_PATTERN, REALTONE_PID, REALTONE_VID
from nocablelauncher.patcher import (
    _find_ids_in_blob,
    _is_wrapper,
    choose_game_pid,
    device_id_bytes,
    find_pattern,
    hex_to_int,
)


class DeviceIdTests(unittest.TestCase):
    def test_realtone_bytes(self):
        self.assertEqual(device_id_bytes("12BA"), REALTONE_VID)
        self.assertEqual(device_id_bytes("00FF"), REALTONE_PID)

    def test_cmedia_adapter(self):
        self.assertEqual(device_id_bytes("0D8C"), bytes((0x8C, 0x0D)))
        self.assertEqual(device_id_bytes("0014"), bytes((0x14, 0x00)))

    def test_leading_zeros(self):
        self.assertEqual(device_id_bytes("14"), bytes((0x14, 0x00)))


class PatternTests(unittest.TestCase):
    def test_finds_signature(self):
        prefix = b"\x00" * 32
        blob = prefix + CABLE_PATTERN + b"\xff" * 8
        self.assertEqual(find_pattern(blob), 32)

    def test_missing(self):
        self.assertIsNone(find_pattern(b"no cable here"))

    def test_zero_ids(self):
        self.assertEqual(device_id_bytes("0000"), bytes((0, 0)))

    def test_rejects_loose_vid_pid_spacing(self):
        blob = b"xx" + REALTONE_VID + b"ABCDEF" + REALTONE_PID + b"yy"
        hits = _find_ids_in_blob(blob, 0x1000)
        self.assertEqual(hits, [])


class WrapperFilterTests(unittest.TestCase):
    def test_skips_bwrap_and_proton(self):
        self.assertTrue(_is_wrapper("bwrap", "bwrap -- proton waitforexitandrun Rocksmith2014.exe"))
        self.assertTrue(_is_wrapper("python3", "python3 .../proton waitforexitandrun Rocksmith2014.exe"))
        self.assertFalse(_is_wrapper("Rocksmith2014.e", r"S:\steamapps\common\Rocksmith2014\Rocksmith2014.exe"))
        self.assertTrue(_is_wrapper("steam.exe", r"c:\windows\system32\steam.exe Rocksmith2014.exe"))

    def test_hex_offsets(self):
        self.assertEqual(hex_to_int("012C96EC"), 0x012C96EC)
        self.assertEqual(hex_to_int("0x012B7E0C"), 0x012B7E0C)


class GamePidChoiceTests(unittest.TestCase):
    def test_skips_leftover_and_blocked(self):
        chosen = choose_game_pid(
            [38579, 31932],
            ignore={38579},
            access={38579: "blocked", 31932: "ok"},
        )
        self.assertEqual(chosen, 31932)

    def test_ignores_blocked_when_nothing_readable(self):
        self.assertIsNone(
            choose_game_pid([38579], ignore=set(), access={38579: "blocked"})
        )


if __name__ == "__main__":
    unittest.main()

class ImageScopedPatchTests(unittest.TestCase):
    def test_scans_only_pe_image_not_heap_copies(self):
        from unittest.mock import patch
        from nocablelauncher import patcher
        module = patcher.ProcessModule(1, 0x400000, 0x1400000, 'Rocksmith2014.exe')
        maps = [
            (0x400000, 0x401000, 'r-xp', 'Rocksmith2014.exe'),
            (0x401000, 0x1800000, 'rwxp', ''),
            (0x16000000, 0x17000000, 'rw-p', ''),
        ]
        with patch.object(patcher, 'locate_module', return_value=module), \
             patch.object(patcher, 'parse_maps', return_value=maps):
            self.assertEqual(patcher._readable_regions(1), [(0x400000, 0x401000), (0x401000, 0x1800000)])

    def test_saved_heap_offset_is_never_reused(self):
        from unittest.mock import patch
        from nocablelauncher import patcher
        module = patcher.ProcessModule(1, 0x400000, 0x1400000, 'Rocksmith2014.exe')
        with patch.object(patcher, 'locate_module', return_value=module), \
             patch.object(patcher, 'read_memory') as read:
            self.assertFalse(patcher.offsets_look_valid(1, 0x167B359E, 0x167B35A6))
        read.assert_not_called()

    def test_pe_size_includes_anonymous_unpacked_sections(self):
        import struct
        from unittest.mock import patch
        from nocablelauncher import patcher
        dos = bytearray(64)
        dos[:2] = b'MZ'
        struct.pack_into('<I', dos, 60, 0x80)
        pe = bytearray(84)
        pe[:4] = b'PE\0\0'
        struct.pack_into('<H', pe, 24, 0x10B)
        struct.pack_into('<I', pe, 80, 0x172e000)
        with patch.object(patcher, 'parse_maps', return_value=[(0x400000, 0x401000, 'r-xp', 'Rocksmith2014.exe')]), \
             patch.object(patcher, 'read_memory', side_effect=[bytes(dos), bytes(pe)]):
            self.assertEqual(patcher.locate_module(1).size, 0x172e000)

    def test_patch_must_read_back_successfully(self):
        from unittest.mock import patch
        from nocablelauncher import patcher
        with patch.object(patcher, 'write_memory'), \
             patch.object(patcher, 'read_memory', return_value=b'\xff\xff'):
            with self.assertRaises(patcher.PatchError):
                patcher.apply_patch(1, '0000', '0000', 0x12B7E0C, 0x12B7E14)
