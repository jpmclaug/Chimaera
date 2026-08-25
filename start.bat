@echo off
title Chimaera // MTG Market Surveillance
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_app.ps1"
if errorlevel 1 pause
