@echo off
call "%~dp0setenv.bat"

setlocal
set ARGS=%*

:: 进入目录并直接运行 Python CLI（阻塞模式）
pushd "%NUNIF_DIR%"
cmd /c python -u -m iw3.cli %ARGS%
popd
