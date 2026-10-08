; Installer Inno Setup 6 cho ThanhDub — thường được gọi qua scripts/build.py:
;   ISCC /DAppVersion=2.2.0 /DSourceDir=<dist\ThanhDub> /DOutputDir=<release> installer\ThanhDub.iss

#ifndef AppVersion
  #define AppVersion "2.2.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\ThanhDub"
#endif
#ifndef OutputDir
  #define OutputDir "..\release"
#endif

#define AppName "ThanhDub"
#define AppExe "ThanhDub.exe"

[Setup]
AppId={{8F3C1B2A-5D7E-4C9A-9B1E-2A6F0D4C7E11}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Trần Thành
AppPublisherURL=https://github.com/mr-f241/ThanhDub
AppSupportURL=https://github.com/mr-f241/ThanhDub/issues
; Cài cho riêng người dùng, không cần quyền admin; app ghi được vào thư mục cài đặt
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={localappdata}\Programs\ThanhDub
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=ThanhDub-{#AppVersion}-setup
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Xoá thư viện của bản cũ trước khi cài đè để không sót file lỗi thời.
; Dữ liệu người dùng (cài đặt, project, model, công cụ tải thêm) nằm ở %USERPROFILE% nên không bị ảnh hưởng.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "portable.txt,data\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
