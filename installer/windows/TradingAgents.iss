; Inno Setup Script for TradingAgents
; 需要先用 PyInstaller 生成 dist/TradingAgents 和 dist/TradingAgentsGUI

#define MyAppName "TradingAgents"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Tauric Research"
#define MyAppURL "https://github.com/TauricResearch/TradingAgents"

[Setup]
AppId={{E5E8C7A4-1F7A-4F21-9C74-9F8F7E8F9B10}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={pf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableDirPage=no
DisableProgramGroupPage=no
; LicenseFile=..\\..\\LICENSE
OutputBaseFilename=TradingAgents-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
WizardStyle=modern
; SetupIconFile=..\..\assets\app.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "其他任务:"; Flags: unchecked

[Files]
; GUI 应用（推荐桌面/开始菜单快捷方式指向）
Source: "..\\..\\dist\\TradingAgentsGUI\\*"; DestDir: "{app}\\TradingAgentsGUI"; Flags: recursesubdirs createallsubdirs
; CLI 应用（可选）
Source: "..\\..\\dist\\TradingAgents\\*"; DestDir: "{app}\\TradingAgents"; Flags: recursesubdirs createallsubdirs
; 许可证和资源
Source: "..\\..\\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\\..\\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\\TradingAgents GUI"; Filename: "{app}\\TradingAgentsGUI\\TradingAgentsGUI.exe"; WorkingDir: "{app}\\TradingAgentsGUI"
Name: "{group}\\TradingAgents CLI (命令行)"; Filename: "{app}\\TradingAgents\\TradingAgents.exe"; WorkingDir: "{app}\\TradingAgents"
Name: "{userdesktop}\\TradingAgents GUI"; Filename: "{app}\\TradingAgentsGUI\\TradingAgentsGUI.exe"; Tasks: desktopicon; WorkingDir: "{app}\\TradingAgentsGUI"

[Run]
Filename: "{app}\\TradingAgentsGUI\\TradingAgentsGUI.exe"; Description: "启动 TradingAgents GUI"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"


