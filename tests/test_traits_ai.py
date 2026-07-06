"""SP-C: opt-in AI session-length classification (user's own Anthropic key,
cached into the per-user traits catalog)."""
import json

import pytest

import models
import traits_ai


# --- models.add_game_traits_entries ------------------------------------------

@pytest.fixture
def traits_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(models, "GAME_TRAITS_PATH", tmp_path / "game_traits.json")
    monkeypatch.setattr(models, "GAME_TRAITS_DEFAULT_PATH",
                        tmp_path / "game_traits.default.json")
    (tmp_path / "game_traits.default.json").write_text(
        json.dumps({"seeded game": {"session_length": "short"}}), encoding="utf-8")
    return tmp_path


def test_add_traits_entries_seeds_from_default(traits_paths):
    models.add_game_traits_entries({"new game": {"session_length": "long"}})
    written = json.loads((traits_paths / "game_traits.json").read_text(encoding="utf-8"))
    assert written["seeded game"] == {"session_length": "short"}
    assert written["new game"] == {"session_length": "long"}


def test_add_traits_entries_merges_into_existing_user_file(traits_paths):
    models.add_game_traits_entries({"a": {"session_length": "short"}})
    models.add_game_traits_entries({"b": {"session_length": "long"}})
    written = json.loads((traits_paths / "game_traits.json").read_text(encoding="utf-8"))
    assert {"seeded game", "a", "b"} <= set(written)
    assert models.load_game_traits()["b"] == {"session_length": "long"}


def test_add_traits_entries_empty_is_noop(traits_paths):
    models.add_game_traits_entries({})
    assert not (traits_paths / "game_traits.json").exists()


def test_add_traits_entries_stable_format(traits_paths):
    models.add_game_traits_entries({"zeta": {"session_length": "short"},
                                    "alpha": {"session_length": "long"}})
    text = (traits_paths / "game_traits.json").read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert text.index('"alpha"') < text.index('"zeta"')


# --- find_unclassified ---------------------------------------------------------

def _add_game(conn, title, session_length=None, source=None):
    conn.execute(
        "INSERT INTO games (title, normalized_title, session_length, "
        "session_length_source) VALUES (?, ?, ?, ?)",
        (title, models.normalize_title(models.clean_title(title)),
         session_length, source))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return gid


def test_find_unclassified_returns_null_session_rows(temp_db):
    conn = models.get_db()
    g1 = _add_game(conn, "Mystery Game")
    _add_game(conn, "Known Short", "short", "catalog")
    _add_game(conn, "Locked Game", "long", "manual")
    rows = traits_ai.find_unclassified(conn)
    assert [r["id"] for r in rows] == [g1]
    assert rows[0]["title"] == "Mystery Game"
    assert rows[0]["normalized_title"] == "mystery game"
    conn.close()


# --- prompt + parse (pure) ------------------------------------------------------

def test_build_batch_prompt_numbers_titles():
    prompt = traits_ai.build_batch_prompt(["Hades", "Persona 5"])
    assert "1. Hades" in prompt
    assert "2. Persona 5" in prompt


def test_parse_classifications_reads_json_object():
    out = traits_ai.parse_classifications(
        'Here you go:\n{"1": "short", "2": "long", "3": "unknown"}', 3)
    assert out == {1: "short", 2: "long", 3: "unknown"}


def test_parse_classifications_drops_invalid_values_and_indices():
    out = traits_ai.parse_classifications(
        '{"1": "SHORT", "2": "medium", "9": "long", "x": "short"}', 2)
    assert out == {1: "short", 2: "unknown"}  # case-normalized; junk -> unknown


def test_parse_classifications_no_json_returns_all_unknown():
    assert traits_ai.parse_classifications("sorry, no idea", 2) == {
        1: "unknown", 2: "unknown"}


# --- classify_unclassified (fake client) ----------------------------------------

class _FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(self.replies.pop(0))


class _FakeClient:
    def __init__(self, replies):
        self.messages = _FakeMessages(replies)


def test_classify_unclassified_writes_db_and_catalog(temp_db, traits_paths):
    conn = models.get_db()
    g1 = _add_game(conn, "Roguelike Thing")
    g2 = _add_game(conn, "Epic RPG")
    g3 = _add_game(conn, "Mystery Meat")
    client = _FakeClient(['{"1": "short", "2": "long", "3": "unknown"}'])
    report = traits_ai.classify_unclassified(conn, client=client, model="test-model")
    assert report == {"total": 3, "classified": 2, "unknown": 1}
    rows = {r["title"]: (r["session_length"], r["session_length_source"])
            for r in conn.execute(
                "SELECT title, session_length, session_length_source FROM games")}
    assert rows["Roguelike Thing"] == ("short", "ai")
    assert rows["Epic RPG"] == ("long", "ai")
    assert rows["Mystery Meat"] == (None, None)   # unknown left NULL, retryable
    catalog = models.load_game_traits()
    assert catalog["roguelike thing"] == {"session_length": "short"}
    assert catalog["epic rpg"] == {"session_length": "long"}
    assert "mystery meat" not in catalog           # unknown never cached
    assert g1 and g2 and g3
    conn.close()


def test_classify_unclassified_batches_and_reports_progress(temp_db, traits_paths, monkeypatch):
    monkeypatch.setattr(traits_ai, "TRAITS_AI_BATCH", 2)
    conn = models.get_db()
    for i in range(3):
        _add_game(conn, f"Game {i}")
    client = _FakeClient(['{"1": "short", "2": "short"}', '{"1": "long"}'])
    ticks = []
    report = traits_ai.classify_unclassified(
        conn, client=client, model="m",
        progress=lambda done, total, found: ticks.append((done, total, found)))
    assert len(client.messages.calls) == 2
    assert report["classified"] == 3
    assert ticks[-1] == (3, 3, 3)
    conn.close()


def test_classify_unclassified_nothing_to_do(temp_db, traits_paths):
    conn = models.get_db()
    _add_game(conn, "Done Already", "short", "catalog")
    client = _FakeClient([])
    report = traits_ai.classify_unclassified(conn, client=client, model="m")
    assert report == {"total": 0, "classified": 0, "unknown": 0}
    assert client.messages.calls == []
    conn.close()


def test_classify_uses_unit_of_play_rule_in_system_instructions():
    """The SP-B key rule rides along on every call (system prompt)."""
    assert "unit of play" in traits_ai.INSTRUCTIONS.lower()
    assert "long" in traits_ai.INSTRUCTIONS and "short" in traits_ai.INSTRUCTIONS


def test_classify_sends_system_instructions(temp_db, traits_paths):
    conn = models.get_db()
    _add_game(conn, "Some Game")
    client = _FakeClient(['{"1": "short"}'])
    traits_ai.classify_unclassified(conn, client=client, model="m")
    assert client.messages.calls[0]["system"] == traits_ai.INSTRUCTIONS
    conn.close()
