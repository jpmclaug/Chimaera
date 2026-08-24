@echo off
title Chimaera // Standalone Surveillance Worker
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_worker.ps1"
if errorlevel 1 pause
