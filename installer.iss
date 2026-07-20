; Inno Setup script â€” wraps dist\findash.exe into findash-setup.exe.
; Build the exe first (see BUILD.md), then compile this with Inno Setup.

#define AppId "findash.terminal.desktop.1"

[Setup]
AppName=findash
AppVersion=1.3.2
AppPublisher=findash
DefaultDirName={autopf}\findash
DefaultGroupName=findash
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=findash-setup
SetupIconFile=findash.ico
UninstallDisplayIcon={app}\findash.exe
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Files]
; One-folder build: copy dist\findash\ (findash.exe + _internal\...) into {app}.
Source: "dist\findash\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "findash.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: ".env.example"; DestDir: "{app}"; Flags: ignoreversion

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Icons]
Name: "{group}\findash"; Filename: "{app}\findash.exe"; IconFilename: "{app}\findash.ico"; AppUserModelID: "{#AppId}"
Name: "{group}\Uninstall findash"; Filename: "{uninstallexe}"
Name: "{userdesktop}\findash"; Filename: "{app}\findash.exe"; IconFilename: "{app}\findash.ico"; AppUserModelID: "{#AppId}"; Tasks: desktopicon

[Run]
; Interactive installs relaunch via this postinstall entry (skipped in silent).
; By the time the user reaches the Finished page the new files have settled, so
; a plain launch works. Silent auto-updates relaunch from [Code] with a delay.
Filename: "{app}\findash.exe"; WorkingDir: "{app}"; Description: "Launch findash"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  rc: Integer;
begin
  // Silent auto-updates (WinSparkle) must relaunch findash themselves, but the
  // relaunch has to WAIT: launched the instant the update finishes, the freshly
  // written numpy C-extension DLLs aren't fully in place yet and the app crashes
  // with "Importing the numpy C-extensions failed". A brief pause lets the file
  // replacement settle â€” a normal (slightly later) launch already works fine.
  if (CurStep = ssPostInstall) and WizardSilent() then
  begin
    Sleep(6000);
    Exec(ExpandConstant('{app}\findash.exe'), '', ExpandConstant('{app}'),
         SW_SHOW, ewNoWait, rc);
  end;
end;
