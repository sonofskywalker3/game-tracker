"""PLATFORM_MEDIA maps platform short_name -> physical media type (cartridge/disc)."""
import app


def test_cartridge_platforms_map_to_cartridge():
    for short in ("Switch", "Switch2", "3DS", "NDS", "N64", "SNES", "NES",
                  "GB", "GBC", "GBA", "Genesis", "Vita"):
        assert app.PLATFORM_MEDIA.get(short) == app.MEDIA_CARTRIDGE, short


def test_disc_platforms_map_to_disc():
    for short in ("PS1", "PS2", "PS3", "PS4", "PS5", "OGXbox", "X360", "Xbox",
                  "GC", "Wii", "WiiU", "Dreamcast", "Saturn", "PSP", "PC"):
        assert app.PLATFORM_MEDIA.get(short) == app.MEDIA_DISC, short


def test_unmapped_platforms_have_no_media():
    # mobile / subscription / unknown carry no physical-media badge
    for short in ("iOS", "Android", "GamePass", "PSPlus", "NSO", "Nonsense"):
        assert app.PLATFORM_MEDIA.get(short) is None, short


def test_media_constants_are_distinct_strings():
    assert app.MEDIA_CARTRIDGE == "cartridge"
    assert app.MEDIA_DISC == "disc"
    assert app.PHYSICAL_FORMATS == ("physical", "both")
