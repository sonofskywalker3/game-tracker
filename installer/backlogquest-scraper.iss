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
; Self-update installs run /VERYSILENT while the app is still tearing down —
; let Restart Manager close it instead of dying on a locked exe.
CloseApplications=force
RestartApplications=no
OutputBaseFilename=BacklogQuest-Scraper-Setup
OutputDir=..\dist
SetupIconFile=..\desktop\assets\backlogquest.ico
UninstallDisplayIcon={app}\BacklogQuest Scraper.exe

[Files]
Source: "..\dist\BacklogQuest Scraper\*"; DestDir: "{app}"; Flags: recursesubdirs
Source: "MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall skipifsourcedoesntexist; Check: not IsWebView2Installed

[Icons]
Name: "{userprograms}\BacklogQuest Scraper"; Filename: "{app}\BacklogQuest Scraper.exe"

[UninstallDelete]
; backlogquest.json is written by [Code] (not tracked by the uninstaller) and
; holds the import token — remove it so uninstall leaves nothing behind.
Type: files; Name: "{app}\backlogquest.json"

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

{ The download server personalizes the installer by NAMING the file
  "BacklogQuest-Scraper-Setup.h-<host>.c-<token>.exe" (the binary itself is
  generic; the server validates host/token charsets before embedding). At
  install time we parse our own filename and write backlogquest.json into
  the install dir; the app's first-run sidecar seeding does the rest. No
  markers in the filename (renamed file, plain download) = no sidecar, and
  the app falls back to its paste-the-token flow. }

function IsTokenChar(C: Char): Boolean;
begin
  Result := (Pos(C, 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-') > 0);
end;

function DecodeConfigFromFileName(const SetupPath: string): string;
var
  Name, Host, Token: string;
  H, C, I: Integer;
begin
  Result := '';
  Name := ExtractFileName(SetupPath);
  H := Pos('.h-', Name);
  C := Pos('.c-', Name);
  if (H = 0) or (C = 0) or (C <= H) then Exit;
  Host := Copy(Name, H + 3, C - (H + 3));
  { Token = chars after ".c-" until anything outside its charset; this also
    strips a browser's duplicate-download suffix like " (1).exe". }
  Token := '';
  I := C + 3;
  while (I <= Length(Name)) and IsTokenChar(Name[I]) do
  begin
    Token := Token + Name[I];
    I := I + 1;
  end;
  if (Host = '') or (Token = '') then Exit;
  Result := '{"server_url": "https://' + Host + '", "token": "' + Token + '"}';
end;

{ The self-updater passes /RELAUNCHAPP=1: relaunch the app when the silent
  install finishes so the update feels like a restart, not an exit. }
function CmdLineParamExists(const Value: string): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Value) = 0 then
    begin
      Result := True;
      Exit;
    end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Cfg: string;
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    Cfg := DecodeConfigFromFileName(ExpandConstant('{srcexe}'));
    if Cfg <> '' then
      SaveStringToFile(ExpandConstant('{app}\backlogquest.json'), Cfg, False);
  end;
  if (CurStep = ssDone) and CmdLineParamExists('/RELAUNCHAPP=1') then
    Exec(ExpandConstant('{app}\BacklogQuest Scraper.exe'), '', '', SW_SHOWNORMAL,
         ewNoWait, ResultCode);
end;
