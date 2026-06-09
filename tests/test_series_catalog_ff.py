import json

import models

CATALOG = json.loads(models.SERIES_CATALOG_DEFAULT_PATH.read_text(encoding="utf-8"))


def _series_values():
    return {e.get("series") for e in CATALOG.values() if isinstance(e, dict)}


def test_ff_numbered_mainline_consolidated_into_final_fantasy():
    """VII/X/XIII/XIV/XV are folded into one 'Final Fantasy' series, not their own."""
    vals = _series_values()
    for merged in ("Final Fantasy VII", "Final Fantasy X", "Final Fantasy XIII",
                   "Final Fantasy XIV", "Final Fantasy XV"):
        assert merged not in vals, f"{merged!r} should be folded into 'Final Fantasy'"
    assert "Final Fantasy" in vals


def test_ff_spinoffs_stay_separate():
    """Distinct sub-franchises remain their own series."""
    vals = _series_values()
    assert "Final Fantasy Crystal Chronicles" in vals
    assert "SaGa" in vals
    assert "Dissidia" in vals
