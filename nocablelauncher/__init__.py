"""Linux rewrite of Maxx53/NoCableLauncher for Proton/Wine."""

__version__ = "1.0.0"
APP_NAME = "NoCable Launcher"
STEAM_APP_ID = "221680"
GAME_PROCESS = "Rocksmith2014"
GAME_EXE = "Rocksmith2014.exe"

# Ubisoft RealTone cable USB IDs (little-endian bytes used in-game).
REALTONE_VID = bytes((0xBA, 0x12))  # 0x12BA
REALTONE_PID = bytes((0xFF, 0x00))  # 0x00FF

# Signature the original launcher scans for after the exe unpacks in RAM.
CABLE_PATTERN = bytes(
    (
        REALTONE_VID[0],
        REALTONE_VID[1],
        0x92,
        0x0A,
        0x10,
        0xC0,
        0x11,
        0xC0,
        REALTONE_PID[0],
        REALTONE_PID[1],
    )
)
