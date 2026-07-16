; Inno Setup script — wraps dist\findash.exe into findash-setup.exe.
; Build the exe first (see BUILD.md), then compile this with Inno Setup.

#define AppId "findash.terminal.desktop.1"

[Setup]
AppName=findash
AppVersion=1.0.3
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
Source: "dist\findash.exe"; DestDir: "{app}"; Flags: ignoreversion
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
