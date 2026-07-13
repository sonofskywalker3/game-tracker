# release_scraper.ps1 — build both flavors and publish to the droplet.
#
# -SkipPublish: build both flavors locally (portable zip + Inno Setup exe) but
# skip the ssh/scp publish steps. Used for local build-and-smoke testing where
# publishing to the droplet is a separate, owner-gated action.
param(
    [switch]$SkipPublish
)

$ErrorActionPreference = "Stop"
$version = (uv run python -c "from desktop.versioncheck import APP_VERSION; print(APP_VERSION)").Trim()
Write-Host "Building BacklogQuest Scraper v$version"

uv run --group desktop-build pyinstaller desktop/build.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }

# Portable zip (folder as zip root).
$portable = "dist/backlogquest-scraper-portable.zip"
if (Test-Path $portable) { Remove-Item $portable }
Compress-Archive -Path "dist/BacklogQuest Scraper" -DestinationPath $portable

# Optional: fetch the tiny WebView2 bootstrapper for the installer to carry.
$bootstrapper = "installer/MicrosoftEdgeWebview2Setup.exe"
if (-not (Test-Path $bootstrapper)) {
  Invoke-WebRequest "https://go.microsoft.com/fwlink/p/?LinkId=2124703" -OutFile $bootstrapper
}

& iscc /DAppVersion=$version installer\backlogquest-scraper.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed (is iscc on PATH?)" }

Set-Content -Path "dist/version.txt" -Value $version -NoNewline

if ($SkipPublish) {
  Write-Host "SkipPublish set: built v$version locally, not publishing to the droplet."
} else {
  ssh gametracker "mkdir -p /opt/backlogquest/scraper"
  scp $portable "dist/BacklogQuest-Scraper-Setup.exe" dist/version.txt gametracker:/opt/backlogquest/scraper/
  Write-Host "Published v$version to the droplet."
}
