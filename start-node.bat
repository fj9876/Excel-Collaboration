@echo off
rem LanExcel v3 启动器：优先独立 exe（免 Python），否则源码模式
cd /d "%~dp0"
if exist "dist\LanExcel.exe" (
    start "" "dist\LanExcel.exe"
) else if exist "dist\LanExcel_win10.exe" (
    start "" "dist\LanExcel_win10.exe"
) else (
    python app.py
)
