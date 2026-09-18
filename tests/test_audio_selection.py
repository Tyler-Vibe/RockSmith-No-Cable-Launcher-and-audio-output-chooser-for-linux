import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from nocablelauncher import devices
from nocablelauncher.devices import CaptureDevice
from nocablelauncher.settings import Settings
from nocablelauncher.wine_audio import _reg_multi_sz, set_alsa_cards


class AudioSelectionTests(unittest.TestCase):
    def test_server_sources_are_selectable_without_alsa(self):
        with patch.object(devices, 'list_alsa_capture_devices', return_value=[]), \
             patch.object(devices, 'list_usb_audio_devices', return_value=[]), \
             patch.object(devices, 'list_pipewire_sources', return_value=[
                 {'name': 'Guitar', 'node': 'guitar.input'},
             ]):
            found = devices.list_capture_devices()
        self.assertEqual(found[0].guid, 'pulse:guitar.input')

    def test_exact_endpoint_match_does_not_choose_similar_profile(self):
        device = CaptureDevice('Headset', '0000', '0000', 'pulse:headset')
        self.assertFalse(devices._sink_matches({'node': 'headset.chat'}, device))
        self.assertTrue(devices._sink_matches({'node': 'headset'}, device))
        self.assertFalse(devices._source_matches({'node': 'headset.mic'}, device))
        self.assertTrue(devices._source_matches({'node': 'headset'}, device))

    def test_card_metadata_overrides_ambiguous_names(self):
        device = CaptureDevice('USB Audio', '0000', '0000', 'alsa:card4', 4, 'Device')
        self.assertFalse(devices._source_matches({'node': 'USB Audio Device', 'card': 5}, device))
        self.assertTrue(devices._source_matches({'node': 'guitar', 'card': '4'}, device))

    def test_failed_default_change_is_not_reported_as_success(self):
        with patch.object(devices.shutil, 'which', return_value='/bin/tool'), \
             patch.object(devices.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
            self.assertFalse(devices._set_default({'id': 2, 'node': 'guitar'}, 'source'))
        with patch.object(devices.shutil, 'which', return_value=None):
            self.assertFalse(devices._set_default({'id': 2, 'node': 'guitar'}, 'source'))

    def test_live_switch_only_moves_game_streams_in_both_directions(self):
        streams = json.dumps([
            {'index': 1, 'properties': {'application.process.binary': 'Rocksmith2014.exe'}},
            {'index': 2, 'properties': {'application.process.binary': 'firefox'}},
            {'index': 3, 'properties': {'application.name': 'Steam'}},
        ])
        with patch.object(devices.shutil, 'which', return_value='/bin/pactl'), \
             patch.object(devices.subprocess, 'check_output', return_value=streams), \
             patch.object(devices.subprocess, 'run') as run:
            self.assertEqual(devices.move_game_streams('guitar', 'headset'), (1, 1, []))
        self.assertEqual([c.args[0] for c in run.call_args_list], [
            ['pactl', 'move-source-output', '1', 'guitar'],
            ['pactl', 'move-sink-input', '1', 'headset'],
        ])

    def test_failed_live_switch_is_reported(self):
        with patch.object(devices.shutil, 'which', return_value='/bin/pactl'), \
             patch.object(devices.subprocess, 'check_output', side_effect=subprocess.TimeoutExpired('pactl', 3)):
            inputs, outputs, errors = devices.move_game_streams('guitar', 'headset')
        self.assertEqual((inputs, outputs), (0, 0))
        self.assertEqual(len(errors), 2)

    def test_server_only_selection_replaces_previous_hardware_cards(self):
        with TemporaryDirectory() as tmp:
            user = Path(tmp) / 'user.reg'
            user.write_text('WINE REGISTRY Version 2\n')
            set_alsa_cards(tmp, 'OldInput', 'OldOutput')
            set_alsa_cards(tmp)
            text = user.read_text()
        self.assertIn('"ALSAInputDevices"=' + _reg_multi_sz('pulse'), text)
        self.assertIn('"ALSAOutputDevices"=' + _reg_multi_sz('pulse'), text)
        self.assertNotIn(_reg_multi_sz('pulse', 'plughw:OldInput,0', 'hw:OldInput'), text)

    def test_cable_emulation_does_not_add_duplicate_default_capture_aliases(self):
        with TemporaryDirectory() as tmp:
            user = Path(tmp) / 'user.reg'
            user.write_text('WINE REGISTRY Version 2\n')
            set_alsa_cards(tmp, 'Device', 'A50')
            set_alsa_cards(tmp, 'Device', 'A50', default_capture_only=True)
            text = user.read_text()
        # Wine adds default itself. An empty extra-device list leaves it alone.
        self.assertIn('"ALSAInputDevices"=' + _reg_multi_sz() + '\n', text)
        self.assertNotIn(_reg_multi_sz('pulse', 'plughw:Device,0', 'hw:Device'), text)
        self.assertIn('"ALSAOutputDevices"=' + _reg_multi_sz('pulse', 'plughw:A50,0', 'hw:A50'), text)


try:
    from nocablelauncher import app
except ImportError:
    app = None


@unittest.skipIf(app is None, 'GTK bindings unavailable')
class LaunchRoutingTests(unittest.TestCase):
    def test_saved_non_guitar_named_input_is_preserved(self):
        device = CaptureDevice('Headset microphone', '0000', '0000', 'pulse:mic')
        combo = Mock()
        window = SimpleNamespace(devices=[device], p1_combo=combo)
        app.LauncherWindow._select_player_device(window, 1, device.guid, '0000', '0000')
        combo.set_active_id.assert_called_once_with(device.guid)

    def test_launch_preparation_routes_selected_input_and_output_and_spoofs_cable(self):
        guitar = CaptureDevice('Selected input', '0000', '0000', 'pulse:guitar')
        output = CaptureDevice('Selected output', '0000', '0000', 'pulse:headset')
        settings = Settings(guid1=guitar.guid, output_guid=output.guid)
        settings.save = Mock()
        window = SimpleNamespace(devices=[guitar], outputs=[output], log=Mock())
        window._apply_output_routing = lambda s, device: app.LauncherWindow._apply_output_routing(window, s, device)
        with patch.object(app, 'pulse_source_name', return_value='guitar'), \
             patch.object(app, 'pulse_sink_name', return_value='headset'), \
             patch.object(app, 'set_default_source', return_value=True) as source, \
             patch.object(app, 'set_default_sink', return_value=True) as sink, \
             patch.object(app, 'set_device_mute', return_value=True), \
             patch.object(app, 'set_alsa_cards', return_value=True) as alsa, \
             patch.object(app, 'write_pulse_asoundrc', return_value=Path('/tmp/asoundrc')):
            ids = app.LauncherWindow._prepare_input(window, settings)
        self.assertEqual(ids, ('0000', '0000'))
        self.assertEqual(settings.pulse_source, 'guitar')
        self.assertEqual(settings.pulse_sink, 'headset')
        self.assertTrue(alsa.call_args.kwargs['default_capture_only'])
        source.assert_called_once_with(guitar)
        sink.assert_called_once_with(output)

    def test_disconnected_input_stops_launch_and_clears_old_route(self):
        settings = Settings(guid1='pulse:missing')
        settings.pulse_source = 'previous'
        window = SimpleNamespace(devices=[], log=Mock())
        with self.assertRaises(app.LaunchError):
            app.LauncherWindow._prepare_input(window, settings)
        self.assertEqual(settings.pulse_source, '')
