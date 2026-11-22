; Laptop AI installer (made with Inno Setup 6, which is free). make_app.bat compiles this for you.
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{B6F1D6E2-4C1B-4B0E-9A53-7D5C2E1A9F10}
AppName=Laptop AI
AppVersion={#AppVersion}
AppPublisher=Tino
DefaultDirName={autopf}\Laptop AI
DefaultGroupName=Laptop AI
DisableProgramGroupPage=yes
; installs for the current user only: no administrator prompt
PrivilegesRequired=lowest
OutputDir=installer_output
OutputBaseFilename=LaptopAI_Setup
SetupIconFile=laptop_ai\assets\app.ico
UninstallDisplayIcon={app}\LaptopAI.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "autostart"; Description: "Start Laptop AI when Windows starts (hidden by the clock)"; GroupDescription: "Options:"; Flags: unchecked

[Files]
Source: "dist\LaptopAI.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Laptop AI"; Filename: "{app}\LaptopAI.exe"
Name: "{autodesktop}\Laptop AI"; Filename: "{app}\LaptopAI.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "LaptopAI"; \
  ValueData: """{app}\LaptopAI.exe"" --minimized"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\LaptopAI.exe"; Description: "Open Laptop AI now"; Flags: nowait postinstall skipifsilent

; Your settings, notes and reminders live in C:\Users\<you>\.laptop_ai and are kept when you uninstall.
