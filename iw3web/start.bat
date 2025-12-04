@echo off
:: 设置代码页为 UTF-8
chcp 65001 >nul

:: 保存当前脚本所在目录
set "SCRIPT_DIR=%~dp0"

:: 检查是否以管理员权限运行
net session >nul 2>&1
if %errorLevel% NEQ 0 (
    echo 正在请求管理员权限...
    goto UACPrompt
)

:: 已经是管理员，切换到脚本所在目录并运行
cd /d "%SCRIPT_DIR%"
python main.py
goto :eof

:UACPrompt
    echo Set UAC = CreateObject^("Shell.Application"^) > "%temp%\getadmin.vbs"
    echo UAC.ShellExecute "cmd.exe", "/c cd /d ""%SCRIPT_DIR%"" && %~s0", "", "runas", 1 >> "%temp%\getadmin.vbs"
    "%temp%\getadmin.vbs"
    del "%temp%\getadmin.vbs" 2>nul
    exit /b