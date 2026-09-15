@echo off
chcp 65001 >nul
cd /d "%~dp0"
C:\Users\Administrator\miniconda3\python.exe playbridge_host.py
pause
