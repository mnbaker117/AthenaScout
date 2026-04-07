; Inno Setup Script for AthenaScout
; Generates a Windows installer with Start Menu shortcuts, optional desktop
; icon, per-user or system-wide install option, and an uninstaller.
;
; Compiled with: iscc.exe installer.iss
; Output: Output\AthenaScout-{VERSION}-Setup.exe

#define MyAppName "AthenaScout"
#define MyAppVersion "{VERSION}"
#define MyAppPublisher "Mark Baker"
#define MyAppURL "https://github.com/mnbaker117/AthenaScout"
#define MyAppExeName "athenascout.exe"

[Setup]
; AppId is a stable UUID that uniquely identifies the app for upgrade detection.
; DO NOT change this between releases — it would create duplicate installs.
AppId={{F8E2A4C9-7B3D-4E1A-8F5C-9D6E2A1B3C4D}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; Default install location — {autopf} resolves to:
;   - "C:\Program Files\AthenaScout" for system-wide install (requires admin)
;   - "%LOCALAPPDATA%\Programs\AthenaScout" for per-user install (no admin)
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes

; Let the user choose per-user vs system-wide at install time
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; Modern wizard appearance
WizardStyle=modern

; Output naming
OutputDir=Output
OutputBaseFilename=AthenaScout-{#MyAppVersion}-Setup
Compression=lzma2
SolidCompression=yes

; License file shown during install
LicenseFile=LICENSE

; Uninstaller appears in Add/Remove Programs
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Copy the entire PyInstaller onedir output to the install directory.
; recursesubdirs handles the _internal/ folder containing all bundled deps.
Source: "dist\athenascout\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu shortcut (always created — no checkbox)
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
; Desktop shortcut (optional, off by default)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Optional: launch the app immediately after install completes
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
