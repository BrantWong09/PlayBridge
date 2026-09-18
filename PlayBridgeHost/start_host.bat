@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
C:\Users\Administrator\miniconda3\python.exe playbridge_host.py > host_console.log 2>&1
pause
