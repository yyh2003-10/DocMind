@echo off
chcp 65001 >nul

REM 版本号唯一来源：改版本只改这一处（打包文件名 / 安装包参数 / 提示文案统一使用）
set APP_VERSION=1.0.2

set ROOT_DIR=%~dp0..
cd /d "%ROOT_DIR%"

echo ============================================================
echo   DocMind Release 一键发布与打包工具 (v%APP_VERSION%)
echo ============================================================

echo [*] 正在执行 dotnet publish 发布 Release win-x64 单文件版本...
dotnet publish DocMind/DocMind.csproj -c Release -r win-x64 --self-contained true /p:PublishSingleFile=true /p:EnableCompressionInSingleFile=true

if %ERRORLEVEL% neq 0 (
    echo [✗] Release 构建失败，请检查编译错误。
    pause
    exit /b 1
)

echo [✓] Release 构建成功！
echo [✓] 发布产物路径: %ROOT_DIR%\DocMind\bin\Release\net8.0-windows\win-x64\publish\DocMind.exe

echo.
echo ============================================================
echo   [*] 构建便携 Python 运行时（python-runtime\，随包分发到 {app}\python）
echo   说明：venv 绑定打包机 Python 绝对路径，用户机器上必然失效；
echo         便携运行时基于 Python embeddable，无任何路径绑定。
echo ============================================================

if exist "python-runtime\python.exe" (
    echo [✓] python-runtime 已存在，跳过构建（如需重建请先删除该目录）
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% neq 0 (
        echo [✗] 未找到 python，请先安装 Python 3.11 并加入 PATH（仅打包机需要）
        pause
        exit /b 1
    )
    call powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1 -BuildPortableRuntime
    if %ERRORLEVEL% neq 0 (
        echo [✗] 便携运行时构建失败
        pause
        exit /b 1
    )
)

echo [✓] 便携运行时就绪

echo.
echo ============================================================
echo   [*] 打包绿色便携版 ZIP (DocMind-v%APP_VERSION%-win-x64.zip)
echo ============================================================

if not exist "installer\Output" mkdir "installer\Output"

if exist "installer\Output\staging" rmdir /s /q "installer\Output\staging"
mkdir "installer\Output\staging"
mkdir "installer\Output\staging\scripts"
mkdir "installer\Output\staging\Assets"

copy "DocMind\bin\Release\net8.0-windows\win-x64\publish\DocMind.exe" "installer\Output\staging\" >nul
copy "DocMind\appsettings.json" "installer\Output\staging\" >nul
copy "LICENSE" "installer\Output\staging\" >nul
copy "NOTICE" "installer\Output\staging\" >nul
copy "THIRD_PARTY_LICENSES.md" "installer\Output\staging\" >nul
copy "scripts\setup.ps1" "installer\Output\staging\scripts\" >nul
copy "scripts\install_optional.py" "installer\Output\staging\scripts\" >nul
xcopy "DocMind\Assets" "installer\Output\staging\Assets\" /s /e /y /q >nul
xcopy "python-runtime" "installer\Output\staging\python\" /s /e /y /q >nul
copy "start.bat" "installer\Output\staging\" >nul

echo [*] 正在压缩为 DocMind-v%APP_VERSION%-win-x64.zip ...
if exist "installer\Output\DocMind-v%APP_VERSION%-win-x64.zip" del /f /q "installer\Output\DocMind-v%APP_VERSION%-win-x64.zip"
powershell.exe -NoProfile -Command "Compress-Archive -Path 'installer\Output\staging\*' -DestinationPath 'installer\Output\DocMind-v%APP_VERSION%-win-x64.zip' -Force"
rmdir /s /q "installer\Output\staging"
echo [✓] 绿色版 ZIP 打包完成: installer\Output\DocMind-v%APP_VERSION%-win-x64.zip

echo.
echo ============================================================
echo   [*] 尝试调用 Inno Setup 编译标准安装包
echo ============================================================

set ISCC_EXE=
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set ISCC_EXE="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set ISCC_EXE="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set ISCC_EXE="C:\Program Files\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\InnoSetup7\ISCC.exe" set ISCC_EXE="C:\Program Files\InnoSetup7\ISCC.exe"

if defined ISCC_EXE (
    echo [*] 找到 Inno Setup 编译器: %ISCC_EXE%
    %ISCC_EXE% /DMyAppVersion=%APP_VERSION% "installer\docmind-setup.iss"
    echo [✓] 安装包编译成功: installer\Output\DocMind-Setup-%APP_VERSION%.exe
) else (
    echo [!] 未检测到本地 Inno Setup 编译器，跳过安装包自动编译。
    echo [!] 如需编译安装包，请安装 Inno Setup 并在 installer\docmind-setup.iss 点击编译。
)

echo.
echo ============================================================
echo   全部发布准备就绪！
echo ============================================================
