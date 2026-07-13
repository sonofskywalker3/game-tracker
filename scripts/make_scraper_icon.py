"""Dev-only: render docs/branding/backlogquest-icon.svg to desktop/assets/backlogquest.ico.
Uses the Playwright browser we already have (no cairosvg dep) + Pillow."""
from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(message)s")
ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "docs" / "branding" / "backlogquest-icon.svg"
OUT = ROOT / "desktop" / "assets" / "backlogquest.ico"
SIZE = 256


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    png_path = OUT.with_suffix(".png")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": SIZE, "height": SIZE})
        page.goto(SVG.as_uri())
        page.locator("svg").screenshot(path=str(png_path), omit_background=True)
        browser.close()
    img = Image.open(png_path).convert("RGBA").resize((SIZE, SIZE))
    img.save(OUT, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    png_path.unlink()
    logging.info("wrote %s", OUT)


if __name__ == "__main__":
    main()
