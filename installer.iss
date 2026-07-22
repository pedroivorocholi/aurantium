; Inno Setup script â€” wraps dist\aurantium.exe into aurantium-setup.exe.
; Build the exe first (see BUILD.md), then compile this with Inno Setup.

#define AppId "aurantium.terminal.desktop.1"

[Setup]
; Pinned to the OLD product name on purpose: the findash-era installs never set an
; explicit AppId, so Inno fell back to using AppName ("findash") as the internal
; identity key. Keeping that same value here is what makes this installer recognized
; as an upgrade of an existing findash install (same Programs-and-Features entry,
; same install dir) instead of a fresh side-by-side "aurantium" install. Do not change
; this even though everything else has been renamed.
AppId=findash
AppName=Aurantium
AppVersion=1.5.3
AppPublisher=Aurantium
DefaultDirName={autopf}\Aurantium
DefaultGroupName=Aurantium
; Force the Start Menu group to actually rename to "Aurantium" on upgrade too —
; otherwise Inno would silently keep reusing the previous install's "findash" group.
UsePreviousGroup=no
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=aurantium-setup
SetupIconFile=aurantium.ico
UninstallDisplayIcon={app}\aurantium.exe
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Files]
; One-folder build: copy dist\aurantium\ (aurantium.exe + _internal\...) into {app}.
Source: "dist\aurantium\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "aurantium.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: ".env.example"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; Same AppId means Setup upgrades in place rather than uninstalling the old findash
; install first, so the renamed exe/icon/shortcuts would otherwise linger forever
; alongside the new ones. Clean up the specific old-named artifacts (harmless no-op
; on a fresh install, or if a path doesn't exist). Covers both per-user and
; per-machine installs since PrivilegesRequired=lowest still allows either.
Type: files; Name: "{app}\findash.exe"
Type: files; Name: "{app}\findash.ico"
Type: files; Name: "{userdesktop}\findash.lnk"
Type: files; Name: "{commondesktop}\findash.lnk"
Type: files; Name: "{userprograms}\findash\findash.lnk"
Type: files; Name: "{userprograms}\findash\Uninstall findash.lnk"
Type: dirifempty; Name: "{userprograms}\findash"
Type: files; Name: "{commonprograms}\findash\findash.lnk"
Type: files; Name: "{commonprograms}\findash\Uninstall findash.lnk"
Type: dirifempty; Name: "{commonprograms}\findash"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Icons]
Name: "{group}\Aurantium"; Filename: "{app}\aurantium.exe"; IconFilename: "{app}\aurantium.ico"; AppUserModelID: "{#AppId}"
Name: "{group}\Uninstall Aurantium"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Aurantium"; Filename: "{app}\aurantium.exe"; IconFilename: "{app}\aurantium.ico"; AppUserModelID: "{#AppId}"; Tasks: desktopicon

[Run]
; Interactive installs relaunch via this postinstall entry (skipped in silent).
; By the time the user reaches the Finished page the new files have settled, so
; a plain launch works. Silent auto-updates relaunch from [Code] with a delay.
Filename: "{app}\aurantium.exe"; WorkingDir: "{app}"; Description: "Launch Aurantium"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  rc: Integer;
begin
  // Silent auto-updates (WinSparkle) must relaunch aurantium themselves, but the
  // relaunch has to WAIT: launched the instant the update finishes, the freshly
  // written numpy C-extension DLLs aren't fully in place yet and the app crashes
  // with "Importing the numpy C-extensions failed". A brief pause lets the file
  // replacement settle â€” a normal (slightly later) launch already works fine.
  if (CurStep = ssPostInstall) and WizardSilent() then
  begin
    Sleep(6000);
    Exec(ExpandConstant('{app}\aurantium.exe'), '', ExpandConstant('{app}'),
         SW_SHOW, ewNoWait, rc);
  end;
end;
