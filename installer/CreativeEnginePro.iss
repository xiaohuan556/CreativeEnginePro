#define MyAppName "CreativeEnginePro"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "CreativeEnginePro"
#define MyAppExeName "CreativeEnginePro.exe"

[Setup]
AppId={{E2AE6C33-127B-45BC-A5B6-99F634E730D1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=CreativeEnginePro_Setup_{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ChangesAssociations=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Files]
Source: "..\dist\CreativeEnginePro.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\xiaohuanpic.ico"; DestDir: "{app}\ProjectIcons"; Flags: ignoreversion
Source: "..\assets\xiaohuanvid.ico"; DestDir: "{app}\ProjectIcons"; Flags: ignoreversion
Source: "..\assets\xiaohuanai.ico"; DestDir: "{app}\ProjectIcons"; Flags: ignoreversion

[Registry]
Root: HKA; Subkey: "Software\Classes\.xiaohuanpic"; ValueType: string; ValueData: "CreativeEnginePro.PictureProject"; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKA; Subkey: "Software\Classes\CreativeEnginePro.PictureProject"; ValueType: string; ValueData: "小欢图片工程"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\CreativeEnginePro.PictureProject\DefaultIcon"; ValueType: string; ValueData: """{app}\ProjectIcons\xiaohuanpic.ico"",0"
Root: HKA; Subkey: "Software\Classes\CreativeEnginePro.PictureProject\shell\open\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

Root: HKA; Subkey: "Software\Classes\.xiaohuanvid"; ValueType: string; ValueData: "CreativeEnginePro.VideoProject"; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKA; Subkey: "Software\Classes\CreativeEnginePro.VideoProject"; ValueType: string; ValueData: "小欢剪辑工程"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\CreativeEnginePro.VideoProject\DefaultIcon"; ValueType: string; ValueData: """{app}\ProjectIcons\xiaohuanvid.ico"",0"
Root: HKA; Subkey: "Software\Classes\CreativeEnginePro.VideoProject\shell\open\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

Root: HKA; Subkey: "Software\Classes\.xiaohuanai"; ValueType: string; ValueData: "CreativeEnginePro.CanvasProject"; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKA; Subkey: "Software\Classes\CreativeEnginePro.CanvasProject"; ValueType: string; ValueData: "小欢 AI 画布工程"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\CreativeEnginePro.CanvasProject\DefaultIcon"; ValueType: string; ValueData: """{app}\ProjectIcons\xiaohuanai.ico"",0"
Root: HKA; Subkey: "Software\Classes\CreativeEnginePro.CanvasProject\shell\open\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
