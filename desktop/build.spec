# desktop/build.spec
"""PyInstaller onedir build. Run from repo root:
uv run --group desktop-build pyinstaller desktop/build.spec --noconfirm
Playwright's node driver is bundled via collect_all; browsers are NOT bundled
(the app uses the user's installed Chrome/Edge via channels).

Note: PyInstaller chdirs into the spec file's own directory (SPECPATH) before
executing it, so paths below are built from SPECPATH's parent (the repo root)
rather than assumed relative to the invocation CWD."""
import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.dirname(SPECPATH)  # noqa: F821 -- SPECPATH is injected by PyInstaller

datas, binaries, hiddenimports = [], [], []
for pkg in ("playwright", "webview"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h
datas += [(os.path.join(ROOT, "desktop", "ui"), "ui")]

a = Analysis([os.path.join(ROOT, "desktop", "main.py")], pathex=[ROOT], datas=datas,
             binaries=binaries, hiddenimports=hiddenimports, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, exclude_binaries=True, name="BacklogQuest Scraper",
          icon=os.path.join(ROOT, "desktop", "assets", "backlogquest.ico"), console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="BacklogQuest Scraper")
