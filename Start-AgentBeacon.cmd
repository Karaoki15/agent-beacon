@echo off
powershell.exe -NoProfile -File "%~dp0scripts\windows\Start-AgentBeacon.ps1" -Lan
if errorlevel 1 pause
