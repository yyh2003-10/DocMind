@echo off
chcp 65001 >nul
echo ============================================================
echo   DocMind Release 一键发布与打包工具 (v1.0.1)
echo ============================================================

set ROOT_DIR=%~dp0..
cd /d "%ROOT_DIR%"

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
echo   [*] 构建后端虚拟环境（core + server 依赖，非 editable）
echo ============================================================

echo [*] 检查 Python 3.11 是否可用...
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [✗] 未找到 python，请先安装 Python 3.11 并加入 PATH
    pause
    exit /b 1
)

echo [*] 删除旧 .venv-slim-new（若存在）...
if exist ".venv-slim-new" (
    rmdir /s /q ".venv-slim-new"
)

echo [*] 创建新 .venv...
python -m venv .venv-slim-new

echo [*] 安装 core + server 锁定依赖（清华镜像）...
call .venv-slim-new\Scripts\python.exe -m pip install -r requirements-core.txt -r requirements-server.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if %ERRORLEVEL% neq 0 (
    echo [✗] 依赖安装失败
    pause
    exit /b 1
)

echo [*] 非 editable 模式安装 doc2mind 本身（--no-deps 保住锁定版本）...
call .venv-slim-new\Scripts\python.exe -m pip install . --no-deps
if %ERRORLEVEL% neq 0 (
    echo [✗] doc2mind 安装失败
    pause
    exit /b 1
)

echo [*] 精简 venv（剔除缓存/测试产物）...
if exist ".venv-slim-new\Lib\site-packages\__pycache__" rmdir /s /q ".venv-slim-new\Lib\site-packages\__pycache__"
for /d /r ".venv-slim-new" %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d"
del /s /q ".venv-slim-new\*.pyc" 2>nul
for /d /r ".venv-slim-new" %%d in (tests) do @if exist "%%d" rmdir /s /q "%%d"

echo [✓] 后端虚拟环境构建完成

echo.
echo ============================================================
echo   [*] 打包绿色便携版 ZIP (DocMind-v1.0.1-win-x64.zip)
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
xcopy "DocMind\Assets" "installer\Output\staging\Assets\" /s /e /y /q >nul
xcopy ".venv-slim-new" "installer\Output\staging\.venv\" /s /e /y /q >nul

echo [*] 正在压缩为 DocMind-v1.0.1-win-x64.zip ...
if exist "installer\Output\DocMind-v1.0.1-win-x64.zip" del /f /q "installer\Output\DocMind-v1.0.1-win-x64.zip"
powershell.exe -NoProfile -Command "Compress-Archive -Path 'installer\Output\staging\*' -DestinationPath 'installer\Output\DocMind-v1.0.1-win-x64.zip' -Force"
rmdir /s /q "installer\Output\staging"
echo [✓] 绿色版 ZIP 打包完成: installer\Output\DocMind-v1.0.1-win-x64.zip

echo.
echo ============================================================
echo   [*] 尝试调用 Inno Setup 编译标准安装包
echo ============================================================

set ISCC_EXE=
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set ISCC_EXE="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set ISCC_EXE="C:\Program Files\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\InnoSetup7\ISCC.exe" set ISCC_EXE="C:\Program Files\InnoSetup7\ISCC.exe"

if defined ISCC_EXE (
    echo [*] 找到 Inno Setup 编译器: %ISCC_EXE%
    %ISCC_EXE% /DMyAppVersion=1.0.1 "installer\docmind-setup.iss"
    echo [✓] 安装包编译成功: installer\Output\DocMind-Setup-1.0.1.exe
) else (
    echo [!] 未检测到本地 Inno Setup 编译器，跳过安装包自动编译。
    echo [!] 如需编译安装包，请安装 Inno Setup 并在 installer\docmind-setup.iss 点击编译。
)

echo.
echo ============================================================
echo   全部发布准备就绪！
echo ============================================================
