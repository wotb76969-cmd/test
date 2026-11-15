#define MyAppName "Chelsy AI"
#define MyAppVersion "0.9.0"
#define MyAppExeName "ChelsyAI.exe"
[Setup]
AppId={{77A09FE5-A6C0-4AA7-9EBC-DC6C1656B9C8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\ChelsyAI
DefaultGroupName={#MyAppName}
OutputDir=installer
OutputBaseFilename=ChelsyAI_Setup_0.9
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
WizardStyle=modern
SetupIconFile=assets\chelsy.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
[Files]
Source: "dist\ChelsyAI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\ChelsyHost\*"; DestDir: "{app}\Host"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\ChelsyClient\*"; DestDir: "{app}\Client"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\ChelsyUpload\*"; DestDir: "{app}\CloudUpload"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\ChelsyMigrate\*"; DestDir: "{app}\Migration"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\ChelsyConfig\*"; DestDir: "{app}\Config"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{autoprograms}\Chelsy AI"; Filename: "{app}\{#MyAppExeName}"
Name: "{autoprograms}\Chelsy AI Host"; Filename: "{app}\Host\ChelsyHost.exe"
Name: "{autoprograms}\Chelsy AI Supabase Setup"; Filename: "{app}\Config\ChelsyConfig.exe"
Name: "{autoprograms}\Chelsy AI Client"; Filename: "{app}\Client\ChelsyClient.exe"
Name: "{autodesktop}\Chelsy AI"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные задачи:"
[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить Chelsy AI"; Flags: nowait postinstall skipifsilent
