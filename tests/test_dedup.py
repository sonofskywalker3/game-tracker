from dedup import base_key, strip_edition_key


def test_base_key_normalizes_via_clean_title():
    # Strips platform-edition suffix + lowercases + drops punctuation.
    assert base_key("Brotato - Nintendo Switch 2 Edition") == "brotato"
    assert base_key("AI: The Somnium Files") == "ai the somnium files"


def test_strip_edition_key_removes_known_qualifier():
    assert strip_edition_key("the outer worlds spacers choice edition") == "the outer worlds"
    assert strip_edition_key("disco elysium the final cut") == "disco elysium"
    assert strip_edition_key("dont starve console edition") == "dont starve"


def test_strip_edition_key_leaves_plain_titles():
    assert strip_edition_key("hollow knight") == "hollow knight"
    # "Together" is not an edition qualifier — must NOT be stripped.
    assert strip_edition_key("dont starve together") == "dont starve together"
