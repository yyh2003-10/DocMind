@echo off
chcp 65001 >nul
echo ============================================================
echo   DocMind Release 一键发布与打包工具
echo ============================================================

set ROOT_DIR=%~dp0..
cd /d "%ROOT_DIR%"

echo [*] 正在执行 dotnet publish 发布 Release win-x64 单文件版本...
dotnet publish DocMind/DocMind.csproj -c Release -r win-x64 --self-contained true /p:PublishSingleFile=true /p:EnableCompressionInSingleFile=true

if %ERRORLEVEL% equ 0 (
    echo [✓] Release 构建成功！
    echo [✓] 发布产物路径: %ROOT_DIR%\DocMind\bin\Release\net8.0-windows\win-x64\publish\DocMind.exe
) else (
    echo [✗] Release 构建失败，请检查编译错误。
)

pause
