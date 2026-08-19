@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title DocMind 极速启动器

echo ========================================================
echo               DocMind 智能知识库系统极速启动
echo ========================================================
echo.

cd /d "%~dp0"

REM 1. 检查 Python 解释器
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python 命令！
    echo 请先安装 Python 3.11 (https://www.python.org/downloads/)
    echo 并务必勾选 "Add python.exe to PATH" 后重试。
    echo.
    pause
    exit /b 1
)

REM 2. 检查或自动创建虚拟环境 .venv
if not exist ".venv\Scripts\python.exe" (
    echo [*] 首次运行：正在自动创建 Python 虚拟环境 (.venv)...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [错误] 创建虚拟环境失败！
        pause
        exit /b 1
    )
    echo [*] 正在安装核心运行依赖 (清华镜像加速)...
    .venv\Scripts\python.exe -m pip install -r requirements-core.txt -r requirements-server.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    .venv\Scripts\python.exe -m pip install -e . --no-deps
    echo [✓] 核心依赖初始化完成！
    echo.
)

REM 3. 检查嵌入模型缓存提示
if not exist "%LOCALAPPDATA%\doc2mind\fastembed_cache" (
    echo [*] 提示：首次使用将在后台自动下载嵌入模型 (~90MB，走国内镜像)。
    echo.
)

REM 4. 检查是否已构建 WPF 客户端，若无则尝试构建
set WPF_EXE=DocMind\bin\Release\net8.0-windows\win-x64\DocMind.exe
set WPF_DEV_EXE=DocMind\bin\Debug\net8.0-windows\DocMind.exe

if exist "%WPF_EXE%" (
    echo [*] 启动 DocMind 桌面客户端 (Release)...
    start "" "%WPF_EXE%"
) else if exist "%WPF_DEV_EXE%" (
    echo [*] 启动 DocMind 桌面客户端 (Debug)...
    start "" "%WPF_DEV_EXE%"
) else (
    where dotnet >nul 2>&1
    if %errorlevel% equ 0 (
        echo [*] 正在首次编译 DocMind WPF 桌面客户端...
        dotnet build DocMind\DocMind.csproj -c Release
        if exist "%WPF_EXE%" (
            start "" "%WPF_EXE%"
        ) else (
            echo [*] 正在以后端服务模式独立运行...
            .venv\Scripts\doc2mind.exe serve
        )
    ) else (
        echo [*] 未检测到 .NET 8 SDK，将启动 DocMind HTTP/MCP 服务端...
        echo 访问地址: http://127.0.0.1:8765
        echo 可通过 AI 工具 (Cursor/Claude) 的 MCP 接入，或安装 .NET 8 运行桌面端。
        .venv\Scripts\doc2mind.exe serve
    )
)

echo [✓] DocMind 已启动就绪！
