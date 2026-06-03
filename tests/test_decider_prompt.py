import models
import decider

PERSONA_LEAKS = ("garage", "shield", "square enix", "3 kids", "9pm", "9-10pm")


def test_system_blocks_order_and_cache_control():
    blocks = decider.build_system_prompt("SNAPSHOT-TEXT")
    assert blocks[0]["type"] == "text" and "session" in blocks[0]["text"].lower()
    assert blocks[1]["text"] == "SNAPSHOT-TEXT"
    assert blocks[1]["cache_control"] == {"type": "ephemeral"}
    assert "SLOT" not in blocks[0]["text"]


def test_instructions_have_no_hardcoded_persona():
    text = decider.build_system_prompt("X")[0]["text"].lower()
    for leak in PERSONA_LEAKS:
        assert leak not in text, f"persona leak: {leak}"


def test_slot_context_uses_db_fields(temp_db):
    conn = models.get_db()
    conn.execute(
        "INSERT INTO slots (label, sort_order, platforms, max_session_minutes, "
        "streamable_only, context_notes) VALUES (?, 0, ?, 60, 1, ?)",
        ("Couch Quick", '["Switch"]', "Living room via Shield. Short sittings."))
    conn.commit()
    slot = dict(conn.execute("SELECT * FROM slots ORDER BY id DESC LIMIT 1").fetchone())
    ctx = decider.build_slot_context(conn, slot)
    assert "Couch Quick" in ctx
    assert "Switch" in ctx
    assert "Living room via Shield" in ctx
    assert "60" in ctx
    conn.close()
