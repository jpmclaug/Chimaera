# =========================================================================
# CHIMAERA // TACTICAL INTELLIGENCE - START SCRIPT
# =========================================================================

$PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $PSScriptRoot

Write-Host ""
Write-Host " ========================================================" -ForegroundColor Cyan
Write-Host "   CHIMAERA // MTG MARKET SURVEILLANCE" -ForegroundColor White
Write-Host "   Sector: MTG-Core | Version: 1.0" -ForegroundColor DarkGray
Write-Host " ========================================================" -ForegroundColor Cyan
Write-Host ""

# Determine Port from .env or default 5050
$Port = 5050
if (Test-Path ".env") {
    $envPortLine = Get-Content ".env" | Where-Object { $_ -match "^PORT\s*=\s*(\d+)" }
    if ($envPortLine -match "^PORT\s*=\s*(\d+)") {
        $Port = [int]$Matches[1]
    }
}

# Check if port is already occupied
$existingConn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existingConn) {
    $procId = $existingConn.OwningProcess
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    Write-Host " [!] Alert: Port $Port is already in use by process $($proc.ProcessName) (PID: $procId)." -ForegroundColor Yellow
    Write-Host " [*] Attempting to open browser to active server: http://localhost:$Port" -ForegroundColor Cyan
    Start-Process "http://localhost:$Port"
    Write-Host " [*] Run .\stop_app.ps1 to terminate the existing server if you wish to restart it." -ForegroundColor DarkGray
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 0
}

# Determine Python executable (check virtual environment first)
$pythonExe = "python"
if (Test-Path "venv\Scripts\python.exe") {
    $pythonExe = "$PSScriptRoot\venv\Scripts\python.exe"
    Write-Host " [SYS:ENV] Utilizing virtual environment: venv" -ForegroundColor DarkGray
} else {
    Write-Host " [SYS:ENV] Utilizing system Python from PATH" -ForegroundColor DarkGray
}

$url = "http://localhost:$Port"
$pidFile = Join-Path $PSScriptRoot ".chimaera.pid"
$PID | Out-File -FilePath $pidFile -Encoding utf8

# Schedule browser launch after server starts
$browserJob = Start-Job -ScriptBlock {
    param($targetUrl, $targetPort)
    $attempts = 0
    while ($attempts -lt 20) {
        Start-Sleep -Milliseconds 500
        $conn = Get-NetTCPConnection -LocalPort $targetPort -State Listen -ErrorAction SilentlyContinue
        if ($conn) {
            Start-Sleep -Milliseconds 400
            Start-Process $targetUrl
            break
        }
        $attempts++
    }
    if ($attempts -ge 20) {
        # Fallback open
        Start-Process $targetUrl
    }
} -ArgumentList $url, $Port

Write-Host " [SYS:INIT] Starting Chimaera Server on $url..." -ForegroundColor Cyan
Write-Host " [SYS:ONLINE] Opening default browser to $url" -ForegroundColor Green
Write-Host " [*] Press Ctrl+C in this window or run .\stop_app.ps1 to shut down." -ForegroundColor DarkGray
Write-Host ""

try {
    # Run the Python application directly in this console so logs are visible
    & $pythonExe app.py
}
finally {
    if (Test-Path $pidFile) {
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    }
    Stop-Job $browserJob -ErrorAction SilentlyContinue | Out-Null
    Remove-Job $browserJob -Force -ErrorAction SilentlyContinue | Out-Null
}
