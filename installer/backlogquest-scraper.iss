; installer/backlogquest-scraper.iss — compile with:
;   iscc /DAppVersion=<version> installer\backlogquest-scraper.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
[Setup]
AppName=BacklogQuest Scraper
AppVersion={#AppVersion}
AppPublisher=BacklogQuest
DefaultDirName={localappdata}\Programs\BacklogQuest Scraper
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=BacklogQuest-Scraper-Setup
OutputDir=..\dist
SetupIconFile=..\desktop\assets\backlogquest.ico
UninstallDisplayIcon={app}\BacklogQuest Scraper.exe

[Files]
Source: "..\dist\BacklogQuest Scraper\*"; DestDir: "{app}"; Flags: recursesubdirs
Source: "MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall skipifsourcedoesntexist; Check: not IsWebView2Installed

[Icons]
Name: "{userprograms}\BacklogQuest Scraper"; Filename: "{app}\BacklogQuest Scraper.exe"

[Run]
; Bootstrap WebView2 if missing (no-op when present).
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; \
  Flags: skipifdoesntexist; Check: not IsWebView2Installed

[Code]
function IsWebView2Installed: Boolean;
begin
  Result := RegKeyExists(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}')
    or RegKeyExists(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}');
end;
