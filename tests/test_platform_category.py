from models import classify_platform


def test_modern_consoles_classify_as_modern():
    for short in ("PS4", "PS5", "Switch", "Xbox"):
        assert classify_platform(short) == "modern_console"


def test_pc_storefronts_classify_as_pc():
    for short in ("PC", "Steam", "GOG", "Epic"):
        assert classify_platform(short) == "pc"


def test_old_systems_classify_as_legacy():
    for short in ("PS3", "PS2", "X360", "Wii", "3DS", "Vita", "SNES"):
        assert classify_platform(short) == "legacy_console"


def test_unknown_defaults_to_modern():
    assert classify_platform("SomeFutureConsole") == "modern_console"
