; ============================================================
; DocMind Inno Setup 安装脚本
;
; 使用方法：
;   1. 先用 dotnet publish 发布 Release 版本：
;      dotnet publish WpfApp1/DocMind.csproj -c Release -r win-x64 --self-contained true /p:PublishSingleFile=true /p:EnableCompressionInSingleFile=true
;   2. 确保 .venv 目录已就绪（通过 setup.ps1 创建）
;   3. 用 Inno Setup Compiler 打开本文件，点击编译即可生成安装包
;
; 输出：installer/Output/DocMind-Setup-{version}.exe
; ============================================================

#define MyAppName "DocMind"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "DocMind Contributors"
#define MyAppURL "https://github.com/yyh2003-10/DocMind"
#define MyAppExeName "DocMind.exe"

; 源文件路径（相对于本 .iss 文件所在目录）
#define WpfPublishDir "..\WpfApp1\bin\Release\net8.0-windows\win-x64\publish"
#define VenvDir "..\.venv-slim-new"
#define ScriptsDir "..\scripts"
#define AssetsDir "..\WpfApp1\Assets"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
; 输出目录
OutputDir=Output
OutputBaseFilename=DocMind-Setup-{#MyAppVersion}
; 压缩
Compression=lzma2
SolidCompression=yes
LZMANumBlockThreads=4
; 界面
WizardStyle=modern
WizardSizePercent=110
; 权限：PerUser 安装（无需管理员）
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; 图标
SetupIconFile=..\WpfApp1\Assets\DocMind.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
; 其他
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
MinVersion=10.0.17763
CloseApplications=force
RestartApplications=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce
Name: "install_gpu"; Description: "安装 GPU 加速包（需 NVIDIA 显卡 + 联网下载 ~2GB）"; GroupDescription: "可选扩展（需要网络连接）:"
Name: "install_ocr"; Description: "安装 OCR 图片文字识别（需联网下载 ~1.5GB）"; GroupDescription: "可选扩展（需要网络连接）:"

[Files]
; ===== 主程序（WPF 单文件发布） =====
Source: "{#WpfPublishDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

; ===== 配置文件 =====
Source: "..\WpfApp1\appsettings.json"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist

; ===== 资源文件 =====
Source: "{#AssetsDir}\*"; DestDir: "{app}\Assets"; Flags: ignoreversion recursesubdirs

; ===== Python 虚拟环境（CPU 核心，已排除 GPU/OCR 大型包） =====
Source: "{#VenvDir}\*"; DestDir: "{app}\.venv"; Flags: ignoreversion recursesubdirs

; ===== 部署脚本（供 WPF 启动时检测/运行） =====
Source: "{#ScriptsDir}\setup.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion

; ===== 许可证 =====
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; 安装完成后可选启动
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

; GPU 安装（当用户勾选时执行）
Filename: "{app}\.venv\Scripts\python.exe"; Parameters: "-m pip install onnxruntime-gpu fastembed[gpu] -i https://pypi.tuna.tsinghua.edu.cn/simple"; StatusMsg: "正在安装 GPU 加速包（约 2GB，需联网等待）…"; Flags: runhidden skipifdoesntexist; Tasks: install_gpu

; OCR 安装（当用户勾选时执行）
Filename: "{app}\.venv\Scripts\python.exe"; Parameters: "-m pip install paddlepaddle paddleocr pillow -i https://pypi.tuna.tsinghua.edu.cn/simple"; StatusMsg: "正在安装 OCR 识别包（约 1.5GB，需联网等待）…"; Flags: runhidden skipifdoesntexist; Tasks: install_ocr

[UninstallDelete]
; 卸载时清理用户数据（可选，需用户确认）
Type: filesandordirs; Name: "{localappdata}\DocMind"

[Code]
// 检查是否已有 DocMind 实例运行
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  // 检查进程是否在运行（通过 WMIC 查询）
  if Exec('cmd.exe', '/c tasklist /FI "IMAGENAME eq DocMind.exe" 2>nul | find /I "DocMind.exe"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    if ResultCode = 0 then
    begin
      if MsgBox('DocMind 正在运行，请先关闭后再安装。是否自动关闭？',
                mbConfirmation, MB_YESNO) = IDYES then
      begin
        Exec('cmd.exe', '/c taskkill /F /IM DocMind.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
        Sleep(1000);
      end
      else
      begin
        Result := False;
      end;
    end;
  end;
end;
