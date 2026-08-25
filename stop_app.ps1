# =========================================================================
# CHIMAERA // TACTICAL INTELLIGENCE - STOP SCRIPT
# =========================================================================

$ErrorActionPreference = "SilentlyContinue"
$PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $PSScriptRoot

Write-Host ""
Write-Host " ========================================================" -ForegroundColor Red
Write-Host "   CHIMAERA // SERVER SHUTDOWN PROTOCOL" -ForegroundColor White
Write-Host "   Terminating surveillance processes..." -ForegroundColor DarkGray
Write-Host " ========================================================" -ForegroundColor Red
Write-Host ""

$Port = 5050
if (Test-Path ".env") {
    $envPortLine = Get-Content ".env" | Where-Object { $_ -match "^PORT\s*=\s*(\d+)" }
    if ($envPortLine -match "^PORT\s*=\s*(\d+)") {
        $Port = [int]$Matches[1]
    }
}

$terminatedCount = 0
$pidFile = Join-Path $PSScriptRoot ".chimaera.pid"

# 1. Terminate PID from .chimaera.pid (tree kill)
if (Test-Path $pidFile) {
    $savedPid = (Get-Content $pidFile).Trim()
    if ($savedPid -match "^\d+$") {
        $proc = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host " [SYS:KILL] Halting process tree for PID $savedPid ($($proc.ProcessName))..." -ForegroundColor Yellow
            & taskkill.exe /F /T /PID $savedPid *>$null
            $terminatedCount++
        }
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

# 2. Terminate any lingering process listening on the port
$connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($connections) {
    foreach ($conn in $connections) {
        $ownerId = $conn.OwningProcess
        if ($ownerId -and $ownerId -ne 0) {
            $proc = Get-Process -Id $ownerId -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Host " [SYS:KILL] Terminating process listening on port $Port (PID: $ownerId, $($proc.ProcessName))..." -ForegroundColor Yellow
                & taskkill.exe /F /T /PID $ownerId *>$null
                $terminatedCount++
            }
        }
    }
}

# 3. Final port check
Start-Sleep -Milliseconds 300
$lingering = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue

if ($lingering) {
    Write-Host " [!] Warning: Port $Port is still occupied by PID $($lingering.OwningProcess)." -ForegroundColor Yellow
} else {
    Write-Host " [SYS:OFFLINE] Chimaera server shutdown complete." -ForegroundColor Green
    Write-Host " [SYS:STATUS] Port $Port is free and released." -ForegroundColor Green
}

Write-Host ""
