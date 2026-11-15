; Chelsy AI 0.9.3 — Host installer for the owner's laptop:
; Desktop UI + Chelsy Host API + Windows Client + Supabase setup + data tools.
; Per-user install (no administrator rights). User data lives in %LOCALAPPDATA%\ChelsyAI\data
; and is NOT removed on uninstall or upgrade.
#define MyAppName "Chelsy AI"
#define MyAppVersion "0.9.3"
#define MyAppExeName "ChelsyAI.exe"
[Setup]
AppId={{77A09FE5-A6C0-4AA7-9EBC-DC6C1656B9C8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Chelsy Group
VersionInfoVersion=0.9.3.0
VersionInfoProductName=Chelsy AI Host
DefaultDirName={localappdata}\Programs\ChelsyAI
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer
OutputBaseFilename=ChelsyAI_Host_Setup_0.9.3
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
WizardStyle=modern
SetupIconFile=assets\chelsy.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
SetupLogging=yes
[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
[Files]
Source: "dist\ChelsyAI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\ChelsyHost\*"; DestDir: "{app}\Host"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\ChelsyClient\*"; DestDir: "{app}\Client"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\ChelsyConfig\*"; DestDir: "{app}\Config"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\ChelsyDB\*"; DestDir: "{app}\Database"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\ChelsyUpload\*"; DestDir: "{app}\CloudUpload"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\ChelsyMigrate\*"; DestDir: "{app}\Migration"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "README_0.9.3_RU.md"; DestDir: "{app}"; Flags: ignoreversion
[Icons]
Name: "{autoprograms}\Chelsy AI"; Filename: "{app}\{#MyAppExeName}"
Name: "{autoprograms}\Chelsy AI Host (сервер для устройств)"; Filename: "{app}\Host\ChelsyHost.exe"
Name: "{autoprograms}\Chelsy AI Client"; Filename: "{app}\Client\ChelsyClient.exe"
Name: "{autoprograms}\Chelsy AI — настройка Supabase"; Filename: "{app}\Config\ChelsyConfig.exe"
Name: "{autodesktop}\Chelsy AI"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные задачи:"
[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить Chelsy AI"; Flags: nowait postinstall skipifsilent
