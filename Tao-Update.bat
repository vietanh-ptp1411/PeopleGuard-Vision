@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
set "VG_BUILD_PYTHON=%~dp0build\venv-gpu\Scripts\python.exe"
if exist "%~dp0build\venv-update\Scripts\python.exe" set "VG_BUILD_PYTHON=%~dp0build\venv-update\Scripts\python.exe"
if not exist "%VG_BUILD_PYTHON%" set "VG_BUILD_PYTHON=python"
"%VG_BUILD_PYTHON%" "%~dp0build\build_update.py" %*
set "VG_BUILD_EXIT=%ERRORLEVEL%"
if not "%VG_BUILD_EXIT%"=="0" echo TAO UPDATE THAT BAI. Xem thong bao va log trong release.
if not defined VG_NO_PAUSE pause
exit /b %VG_BUILD_EXIT%
