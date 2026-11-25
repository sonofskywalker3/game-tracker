# Art sources

Where each piece of artwork in this repository comes from and how it was made. Game cover art
shown in the app is fetched from IGDB at run time and is not stored here.

| Files | What it is | How it was made |
| --- | --- | --- |
| [`docs/branding/backlogquest-icon.svg`](docs/branding/backlogquest-icon.svg) | The BacklogQuest mark: a pixel sword through a stack of game cases | Drawn as SVG code by generative AI (Claude, Anthropic) |
| [`static/favicon.svg`](static/favicon.svg) | Web favicon and nav mark | The same sword mark, as SVG code written by generative AI (Claude, Anthropic) |
| [`android/app/src/main/res/drawable/ic_launcher_foreground.xml`](android/app/src/main/res/drawable/ic_launcher_foreground.xml), [`ic_launcher_monochrome.xml`](android/app/src/main/res/drawable/ic_launcher_monochrome.xml) | Adaptive launcher icon with a monochrome layer | The same sword mark, converted to an Android vector by generative AI (Claude, Anthropic) |
| [`desktop/assets/backlogquest.ico`](desktop/assets/backlogquest.ico) | Desktop app icon | Rendered from `backlogquest-icon.svg` by [`scripts/make_scraper_icon.py`](scripts/make_scraper_icon.py); no changes to the drawing |
| [`android/app/src/main/res/drawable/ic_widget_prev.xml`](android/app/src/main/res/drawable/ic_widget_prev.xml), [`ic_widget_next.xml`](android/app/src/main/res/drawable/ic_widget_next.xml) | Widget arrow buttons | Google's [Material Icons](https://github.com/google/material-design-icons) chevron_left and chevron_right (Apache License 2.0), recoloured |
| [`chrome-extension/icons/icon16.png`](chrome-extension/icons/icon16.png), [`icon48.png`](chrome-extension/icons/icon48.png), [`icon128.png`](chrome-extension/icons/icon128.png) | Chrome extension icons | Drawn by [`chrome-extension/icons/generate-icons.html`](chrome-extension/icons/generate-icons.html), a drawing script written by generative AI (Claude, Anthropic) |

## Replacements welcome

I would rather use art made by people. If you would like to draw a replacement for any piece
above, open an issue or a pull request: it will be used with full credit to you, under the
licence you choose.
