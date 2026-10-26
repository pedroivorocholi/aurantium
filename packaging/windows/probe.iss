; Throwaway probe: capture the environment Inno passes to a silent [Run] child.
[Setup]
; Paths resolve from the repo root, two levels up from this script.
SourceDir=..\..
AppName=probe
AppVersion=1
DefaultDirName={localappdata}\aurantium-probe
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=probe-setup
PrivilegesRequired=lowest
Uninstallable=no

[Run]
Filename: "{cmd}"; Parameters: "/C (echo TEMP=%TEMP% & echo TMP=%TMP% & echo CD=%CD%) > ""{app}\envlog.txt"""; Flags: runhidden; Check: WizardSilent
