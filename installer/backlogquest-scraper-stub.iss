; installer/backlogquest-scraper-stub.iss — ~2MB bootstrap the BROWSER downloads
; (small enough that Chrome's deep scan is instant). It fetches the full
; installer from the server and runs it. Compile with:
;   iscc /DAppVersion=<version> installer\backlogquest-scraper-stub.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
[Setup]
AppName=BacklogQuest Scraper
AppVersion={#AppVersion}
AppPublisher=BacklogQuest
CreateAppDir=no
Uninstallable=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=BacklogQuest-Scraper-Stub
OutputDir=..\dist
SetupIconFile=..\desktop\assets\backlogquest.ico

[Messages]
ReadyLabel1=Ready to download and install BacklogQuest Scraper.
ReadyLabel2a=Setup will download the app from your BacklogQuest server (this skips your browser's slow scan of large files), then install it.

[Code]
const
  DefaultHost = 'backlogquest.xyz';

var
  DownloadPage: TDownloadWizardPage;
  PayloadExitCode: Integer;

{ Same marker convention as the full installer: the download server names this
  stub "...h-<host>.c-<token>.exe". The stub only needs <host> (to know where
  to download from) but preserves the WHOLE name onto the payload so the full
  installer's own filename-decoding writes the sidecar unchanged. }
function HostFromFileName(const SetupPath: string): string;
var
  Name: string;
  H, C, I: Integer;
begin
  Result := DefaultHost;
  Name := ExtractFileName(SetupPath);
  H := Pos('.h-', Name);
  C := Pos('.c-', Name);
  if (H = 0) or (C = 0) or (C <= H) then Exit;
  Result := Copy(Name, H + 3, C - (H + 3));
  if Result = '' then Result := DefaultHost;
end;

{ Payload keeps the stub's own filename so markers survive; strip a browser's
  " (1)" duplicate suffix is unnecessary — the token parser in the full
  installer already stops at the first out-of-charset character. }
function PayloadFileName: string;
begin
  Result := ExtractFileName(ExpandConstant('{srcexe}'));
end;

function OnDownloadProgress(const Url, FileName: string; const Progress, ProgressMax: Int64): Boolean;
begin
  Result := True;
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing),
    'Downloading BacklogQuest Scraper — this skips your browser''s slow scan of large files.',
    @OnDownloadProgress);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Host: string;
begin
  Result := True;
  if CurPageID = wpReady then
  begin
    Host := HostFromFileName(ExpandConstant('{srcexe}'));
    DownloadPage.Clear;
    DownloadPage.Add('https://' + Host + '/download/scraper/payload', PayloadFileName, '');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
      except
        SuppressibleMsgBox('Download failed from https://' + Host +
          '/download/scraper/payload' + #13#10 + GetExceptionMessage,
          mbCriticalError, MB_OK, IDOK);
        Result := False;
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Args: string;
begin
  if CurStep = ssPostInstall then
  begin
    { Forward silent mode so a scripted "stub /VERYSILENT" stays silent
      end-to-end; interactive runs get the full installer's normal wizard. }
    Args := '';
    if WizardSilent then
      Args := '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART';
    if not Exec(ExpandConstant('{tmp}\') + PayloadFileName, Args, '',
                SW_SHOWNORMAL, ewWaitUntilTerminated, PayloadExitCode) then
      SuppressibleMsgBox('Could not start the downloaded installer.',
        mbCriticalError, MB_OK, IDOK);
  end;
end;

function GetCustomSetupExitCode: Integer;
begin
  Result := PayloadExitCode;   { 0 on success; surfaces the inner install's failure }
end;
