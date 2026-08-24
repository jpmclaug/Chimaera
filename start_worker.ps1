# =========================================================================
# CHIMAERA // IMPERIAL TACTICAL INTELLIGENCE - STANDALONE WORKER START SCRIPT
# =========================================================================

$PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $PSScriptRoot

Write-Host ""
Write-Host " ========================================================" -ForegroundColor Cyan
Write-Host "   CHIMAERA // STANDALONE SURVEILLANCE WORKER" -ForegroundColor White
Write-Host "   Sector: MTG-Core | Version: 1.0 (ISD-72)" -ForegroundColor DarkGray
Write-Host " ========================================================" -ForegroundColor Cyan
Write-Host ""

# Determine Python executable (check virtual environment first)
$pythonExe = "python"
if (Test-Path "venv\Scripts\python.exe") {
    $pythonExe = "$PSScriptRoot\venv\Scripts\python.exe"
    Write-Host " [SYS:ENV] Utilizing virtual environment: venv" -ForegroundColor DarkGray
} else {
    Write-Host " [SYS:ENV] Utilizing system Python from PATH" -ForegroundColor DarkGray
}

Write-Host " [SYS:INIT] Starting standalone price surveillance worker..." -ForegroundColor Cyan
Write-Host " [*] Press Ctrl+C in this window to shut down worker." -ForegroundColor DarkGray
Write-Host ""

& $pythonExe worker.py
