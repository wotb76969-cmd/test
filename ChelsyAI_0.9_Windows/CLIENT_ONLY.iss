#define ClientName "Chelsy AI Client"
#define ClientVersion "0.9.0"
[Setup]
AppId={{8A7D3406-1380-4359-8769-3BC1DD32C099}
AppName={#ClientName}
AppVersion={#ClientVersion}
DefaultDirName={localappdata}\Programs\ChelsyAIClient
DefaultGroupName=Chelsy AI
OutputDir=installer
OutputBaseFilename=ChelsyAI_Client_Setup_0.9
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
WizardStyle=modern
SetupIconFile=assets\chelsy.ico
UninstallDisplayIcon={app}\ChelsyClient.exe
ArchitecturesAllowed=x64compatible
[Files]
Source: "dist\ChelsyClient\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\ChelsyConfig\*"; DestDir: "{app}\Config"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{autoprograms}\Chelsy AI Client"; Filename: "{app}\ChelsyClient.exe"
Name: "{autoprograms}\Chelsy Supabase Setup"; Filename: "{app}\Config\ChelsyConfig.exe"
Name: "{autodesktop}\Chelsy AI Client"; Filename: "{app}\ChelsyClient.exe"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные задачи:"
[Run]
Filename: "{app}\ChelsyClient.exe"; Description: "Запустить Chelsy AI Client"; Flags: nowait postinstall skipifsilent
