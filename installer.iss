; Zapret Control — Inno Setup 6
; Place installer.iss in the project root, alongside dist\ZapretControl\
; Build with Inno Setup Compiler or ISCC.exe installer.iss

#define MyAppName "Zapret Control"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "Zapret Control"
#define MyAppExeName "ZapretControl.exe"

[Setup]
AppId={{A44B5C72-2FC0-4E7A-A7C4-1A9E9E225D67}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\ZapretControl
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputDir=installer_output
OutputBaseFilename=ZapretControl_Setup_v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no
; Use no SetupIconFile until you have a real .ico

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"; Flags: unchecked

[Files]
; Программа и интерфейс — обновляются штатно
Source: "dist\ZapretControl\*"; DestDir: "{app}"; \
    Excludes: "Zapret_dir\*"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; Движок zapret — устанавливается только если файлов ещё нет
; При обновлении существующие файлы не перезаписываются
Source: "dist\ZapretControl\Zapret_dir\*"; \
    DestDir: "{app}\Zapret_dir"; \
    Flags: onlyifdoesntexist recursesubdirs createallsubdirs uninsneveruninstall

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall skipifsilent
