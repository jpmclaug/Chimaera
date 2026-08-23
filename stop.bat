@echo off
title Chimaera // Stop Server
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_app.ps1"
pause
