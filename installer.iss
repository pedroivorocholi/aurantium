; Inno Setup script — wraps dist\findash.exe into findash-setup.exe.
; Build the exe first (see BUILD.md), then compile this with Inno Setup.

#define AppId "findash.terminal.desktop.1"

[Setup]
AppName=findash
AppVersion=1.0.1
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
Filename: "{app}\findash.exe"; Description: "Launch findash"; Flags: nowait postinstall skipifsilent
; Silent WinSparkle updates skip the postinstall entry above (skipifsilent),
; and WinSparkle doesn't relaunch the app itself — so relaunch findash here
; whenever the install runs silently (i.e. an auto-update).
Filename: "{app}\findash.exe"; Flags: nowait; Check: WizardSilent
