; Inštalátor pre Windows (Inno Setup 6) – zostavuje ho GitHub Actions
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6B0C2B9E-4D1F-4C55-9E7A-2F3D8A1C5E77}
AppName=Kompilátor Cura → Ender-3 Pro
AppVersion={#AppVersion}
AppPublisher=Stefan Jancar
DefaultDirName={localappdata}\Programs\KompilatorCura
DefaultGroupName=Kompilátor Cura
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=KompilatorCura-Setup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\KompilatorCura.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "sk"; MessagesFile: "compiler:Languages\Slovak.isl"

[Tasks]
Name: "desktopicon"; Description: "Ikona na ploche"; GroupDescription: "Ďalšie možnosti:"

[Files]
Source: "..\dist\KompilatorCura\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Kompilátor Cura"; Filename: "{app}\KompilatorCura.exe"
Name: "{autodesktop}\Kompilátor Cura"; Filename: "{app}\KompilatorCura.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\KompilatorCura.exe"; Description: "Spustiť Kompilátor Cura"; Flags: nowait postinstall skipifsilent
