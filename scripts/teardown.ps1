# ===========================================================
# IoT Protocol Test Harness - Teardown & Cleanup Script (PowerShell)
# File: scripts/teardown.ps1
# Usage: .\scripts\teardown.ps1
# ===========================================================

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " IoT Protocol Test Harness - Teardown & Environment Cleanup" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Stop background Leshan Java client
Write-Host "[ 1/4 ] Stopping Leshan Java LwM2M client..." -ForegroundColor Magenta
try {
    $leshanProcs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -match "leshan-client-demo.jar"
    }
    if ($leshanProcs) {
        foreach ($proc in $leshanProcs) {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "  -> Stopped Leshan process ID: $($proc.ProcessId)" -ForegroundColor Green
        }
    } else {
        Write-Host "  -> No running Leshan Java process found." -ForegroundColor Gray
    }
} catch {
    Write-Host "  -> Check failed: $_" -ForegroundColor Yellow
}

# 2. Stop Python background simulators if any
Write-Host "[ 2/4 ] Stopping background LoRaWAN forwarders..." -ForegroundColor Magenta
try {
    $loraProcs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -match "lorawan_udp_forwarder.py"
    }
    if ($loraProcs) {
        foreach ($proc in $loraProcs) {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "  -> Stopped LoRaWAN forwarder process ID: $($proc.ProcessId)" -ForegroundColor Green
        }
    } else {
        Write-Host "  -> No running LoRaWAN forwarder process found." -ForegroundColor Gray
    }
} catch {
    Write-Host "  -> Check failed: $_" -ForegroundColor Yellow
}

# 3. Docker Compose teardown (optional profile)
Write-Host "[ 3/4 ] Checking Docker Compose services..." -ForegroundColor Magenta
if (Get-Command docker -ErrorAction SilentlyContinue) {
    if (Test-Path "docker\docker-compose.yml") {
        Write-Host "  -> Running docker compose down for profile lorawan-sim..." -ForegroundColor Gray
        docker compose -f docker\docker-compose.yml --profile lorawan-sim down -v --remove-orphans 2>$null
    }
} else {
    Write-Host "  -> Docker not installed; skipping docker compose down." -ForegroundColor Gray
}

# 4. Clean temporary files and caches
Write-Host "[ 4/4 ] Cleaning temporary test files and pycache..." -ForegroundColor Magenta
$tempPatterns = @("logs\leshan_*.log", "logs\lorawan_*.log", "logs\*.tmp", "logs\*.lock")
foreach ($pat in $tempPatterns) {
    Get-ChildItem -Path $pat -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
}

Get-ChildItem -Path . -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path . -Recurse -Directory -Filter ".pytest_cache" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "  -> Temp files and __pycache__ cleaned." -ForegroundColor Green
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Teardown complete. Environment cleaned." -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
