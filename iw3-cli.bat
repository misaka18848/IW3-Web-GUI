@echo off
call "%~dp0setenv.bat"

:: 将所有传入参数保存到 ARGS 变量中
setlocal
set ARGS=%*

:: 进入目录并启动 Python CLI，传入参数
pushd "%NUNIF_DIR%" && start "" python -m iw3.cli %ARGS% && popd
