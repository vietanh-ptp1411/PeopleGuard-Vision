@echo off
rem VisionGuard - chay ngay (kiem tra truoc, tu khoi dong lai neu tat bat thuong)
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" tools\launcher.py %*
) else (
    python tools\launcher.py %*
)
