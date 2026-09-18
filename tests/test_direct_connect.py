import unittest

from nocablelauncher.direct_connect import (
    add_mission_direct_connect,
    add_startup_othercable,
    mission_has_direct_connect,
    startup_has_othercable,
)
from nocablelauncher.game_json import fix_game_json
import json


class GameJsonTests(unittest.TestCase):
    def test_inserts_missing_commas(self):
        raw = '{"a": {"b": 1}\n"c": 2,}'
        parsed = json.loads(fix_game_json(raw))
        self.assertEqual(parsed["c"], 2)


class DirectConnectJsonTests(unittest.TestCase):
    def test_adds_menu_entries(self):
        startup = {
            "Static": {
                "UI": {
                    "Menus": {
                        "Entries": {
                            "FE_InputSelect": {
                                "View": {"Definition": {"Images": {}, "Buttons": {}}}
                            }
                        }
                    }
                }
            }
        }
        mission = {
            "Static": {
                "UI": {
                    "Menus": {
                        "Entries": {
                            "MissionMenu": {
                                "View": {
                                    "Definition": {
                                        "Buttons": {"inputMode": {"AcceptedValues": {"0": "RTC"}}}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        self.assertFalse(startup_has_othercable(startup))
        add_startup_othercable(startup)
        add_mission_direct_connect(mission)
        self.assertTrue(startup_has_othercable(startup))
        self.assertTrue(mission_has_direct_connect(mission))
        self.assertEqual(mission["Static"]["UI"]["Menus"]["Entries"]["MissionMenu"]["View"]["Definition"]["Buttons"]["inputMode"]["AcceptedValues"]["3"], "DIRECT CONNECT")


if __name__ == "__main__":
    unittest.main()
