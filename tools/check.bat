@echo off
rem VisionGuard - kiem tra he thong, khong chay phan mem
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" tools\preflight.py
) else (
    python tools\preflight.py
)
pause
