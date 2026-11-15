; Chelsy AI 0.9.3 — standalone Windows Client. Does NOT need Ollama.
; Connects to Chelsy Host over HTTPS (Tailscale) and signs in with the Chelsy Group account.
#define ClientName "Chelsy AI Client"
#define ClientVersion "0.9.3"
[Setup]
AppId={{8A7D3406-1380-4359-8769-3BC1DD32C099}
AppName={#ClientName}
AppVersion={#ClientVersion}
AppVerName={#ClientName} {#ClientVersion}
AppPublisher=Chelsy Group
VersionInfoVersion=0.9.3.0
VersionInfoProductName=Chelsy AI Client
DefaultDirName={localappdata}\Programs\ChelsyAIClient
DefaultGroupName=Chelsy AI
DisableProgramGroupPage=yes
OutputDir=installer
OutputBaseFilename=ChelsyAI_Client_Setup_0.9.3
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
WizardStyle=modern
SetupIconFile=assets\chelsy.ico
UninstallDisplayIcon={app}\ChelsyClient.exe
UninstallDisplayName={#ClientName} {#ClientVersion}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
SetupLogging=yes
[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
[Files]
Source: "dist\ChelsyClient\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\ChelsyConfig\*"; DestDir: "{app}\Config"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\Установка.md"; DestDir: "{app}"; Flags: ignoreversion
[Icons]
Name: "{autoprograms}\Chelsy AI Client"; Filename: "{app}\ChelsyClient.exe"
Name: "{autoprograms}\Chelsy AI Client — настройка Supabase"; Filename: "{app}\Config\ChelsyConfig.exe"
Name: "{autodesktop}\Chelsy AI Client"; Filename: "{app}\ChelsyClient.exe"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные задачи:"
[Run]
Filename: "{app}\ChelsyClient.exe"; Description: "Запустить Chelsy AI Client"; Flags: nowait postinstall skipifsilent
