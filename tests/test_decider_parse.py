import decider


def test_parse_strips_block_and_returns_ids():
    text = "I'd go with Hades.\n<suggestions>12, 88</suggestions>"
    reply, ids = decider.parse_suggestions(text, valid_ids={12, 88, 99})
    assert reply == "I'd go with Hades."
    assert ids == [12, 88]


def test_parse_drops_invalid_ids():
    reply, ids = decider.parse_suggestions("ok <suggestions>5,7</suggestions>", valid_ids={5})
    assert ids == [5]


def test_parse_no_block_returns_text_and_empty():
    reply, ids = decider.parse_suggestions("Just a question?", valid_ids={1})
    assert reply == "Just a question?" and ids == []


def test_parse_empty_block():
    reply, ids = decider.parse_suggestions("What's your energy? <suggestions></suggestions>",
                                           valid_ids={1})
    assert ids == [] and reply == "What's your energy?"
