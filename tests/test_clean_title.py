"""Pure unit tests for the display-title cleaning rules (workstream 2, Part A).

These cover non-name junk removal only. No algorithmic re-casing of normal-case
titles — authoritative casing comes from the IGDB canonical pass (Part B).
"""
from models import clean_title


# --- leading region/language tag (extensible LEADING_TAGS) -----------------

def test_strips_leading_region_tag():
    assert clean_title("(English) Pokémon FireRed Version") == "Pokémon FireRed Version"


def test_strips_other_leading_region_tags():
    assert clean_title("(USA) Some Game") == "Some Game"
    assert clean_title("(Europe) Some Game") == "Some Game"


def test_keeps_leading_parenthetical_that_is_not_a_known_tag():
    # Table-driven: only known region/language tags are stripped, not any paren.
    assert clean_title("(Konami) Contra") == "(Konami) Contra"


# --- stray/surrounding straight double quotes ------------------------------

def test_strips_surrounding_quotes_in_bundle_name():
    assert clean_title('"Edna & Harvey" Bundle') == "Edna & Harvey Bundle"


def test_strips_fully_quoted_title():
    assert clean_title('"Hello Neighbor"') == "Hello Neighbor"


def test_keeps_apostrophes():
    # Single quotes are part of names and must never be stripped.
    assert clean_title("Don't Starve") == "Don't Starve"
    assert clean_title("Harvey's New Eyes") == "Harvey's New Eyes"


# --- known platform-edition suffixes (extensible KNOWN_EDITION_SUFFIXES) ----

def test_strips_dash_joined_edition_suffix():
    assert clean_title("Fantasy Life i - Nintendo Switch 2 Edition") == "Fantasy Life i"


def test_strips_colon_joined_edition_suffix():
    assert clean_title("Some Game: Nintendo Switch Edition") == "Some Game"


def test_strips_space_joined_edition_suffix():
    assert clean_title("Some Game Nintendo Switch 2 Edition") == "Some Game"


# --- no casing guessing on normal-case titles ------------------------------

def test_does_not_recase_normal_title():
    assert clean_title("Hollow Knight") == "Hollow Knight"


def test_does_not_recase_lowercase_article_after_colon():
    # Part A leaves casing alone; "Ai: the" is fixed authoritatively in Part B.
    assert clean_title("Ai: the Somnium Files") == "Ai: the Somnium Files"


# --- existing behavior preserved -------------------------------------------

def test_all_caps_title_still_title_cased():
    assert clean_title("DRAGON QUEST XI") == "Dragon Quest XI"


def test_trailing_platform_paren_still_stripped():
    assert clean_title("Celeste (Switch)") == "Celeste"


def test_trademark_symbols_still_removed():
    assert clean_title("Bayonetta™") == "Bayonetta"


# --- idempotency + combined --------------------------------------------------

def test_idempotent():
    messy = '(English) "Some Game" - Nintendo Switch 2 Edition'
    once = clean_title(messy)
    assert clean_title(once) == once


def test_combined_rules():
    assert clean_title('(English) "Some Game" - Nintendo Switch 2 Edition') == "Some Game"
