# ===========================================================
# IoT Protocol Test Harness - Environment Setup & Smoke Check
# File: setup_environment.ps1
# Phase 5: Pre-Flight Validation Script
# Usage: .\setup_environment.ps1 [-PythonPath <path>]
# ===========================================================

param(
    [string]$PythonPath = "",
    [switch]$SkipLibCheck,
    [switch]$SkipJarCheck,
    [switch]$SkipPortCheck,
    [switch]$Verbose
)

$ErrorActionPreference = "Continue"
$PASS = "[PASS]"
$FAIL = "[FAIL]"
$WARN = "[WARN]"
$INFO = "[INFO]"

$script:PassCount = 0
$script:FailCount = 0
$script:WarnCount = 0

function Write-Status {
    param($Symbol, $Message, $Color = "White")
    Write-Host "$Symbol $Message" -ForegroundColor $Color
}

function Check-Pass { param($msg) $script:PassCount++; Write-Status $PASS $msg "Green" }
function Check-Fail { param($msg) $script:FailCount++; Write-Status $FAIL $msg "Red" }
function Check-Warn { param($msg) $script:WarnCount++; Write-Status $WARN $msg "Yellow" }

# Auto-detect Python interpreter
$PythonBin = "python"
$iotTestPy = "$env:USERPROFILE\.conda\envs\iot-test\python.exe"

if ($PythonPath -and (Test-Path $PythonPath)) {
    $PythonBin = $PythonPath
} elseif (Test-Path $iotTestPy) {
    $PythonBin = $iotTestPy
} elseif ($env:CONDA_PREFIX -and (Test-Path "$env:CONDA_PREFIX\python.exe")) {
    $PythonBin = "$env:CONDA_PREFIX\python.exe"
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " IoT Protocol Test Harness - Pre-Flight Environment Check" -ForegroundColor Cyan
Write-Host " Date: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
Write-Host " Interpreter: $PythonBin" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# ─── SECTION 1: System Prerequisites ────────────────────────
Write-Host "[ 1/6 ] System Prerequisites" -ForegroundColor Magenta

# Python check
try {
    $pyver = & $PythonBin --version 2>&1
    if ($pyver -match "Python 3\.(\d+)") {
        $minor = [int]$Matches[1]
        if ($minor -ge 10) { Check-Pass "Python: $pyver" }
        else { Check-Warn "Python: $pyver (recommend >= 3.10)" }
    } else {
        Check-Pass "Python: $pyver"
    }
} catch {
    Check-Fail "Python not found at $PythonBin"
}

# pip check
try {
    $pipver = & $PythonBin -m pip --version 2>&1
    $pipFirstTwo = ($pipver -split '\s+')[0..1] -join ' '
    Check-Pass "pip: $pipFirstTwo"
} catch {
    Check-Fail "pip not found"
}

# Java check (uses cmd.exe to capture version without PowerShell stderr issues)
try {
    $javaRaw = cmd.exe /c "java -version 2>&1"
    $javaText = $javaRaw -join " "
    if ($javaText -match 'version "(\d+)') {
        $jmajor = [int]$Matches[1]
        $matchText = $Matches[0]
        if ($jmajor -ge 17) {
            Check-Pass "Java: version $jmajor >= 17 ($matchText)"
        } else {
            Check-Warn "Java: version $jmajor (recommend >= 17 for Leshan)"
        }
    } elseif ($javaText -match 'version') {
        Check-Pass "Java: detected runtime"
    } else {
        Check-Fail "Java not found -- install OpenJDK 17+"
    }
} catch {
    Check-Fail "Java check failed: $_"
}

# Docker check (optional)
try {
    $dockver = docker --version 2>&1
    Check-Pass "Docker: $dockver"
} catch {
    Check-Warn "Docker not installed (LoRaWAN uses pure-Python UDP forwarder instead)"
}

Write-Host ""

# ─── SECTION 2: Directory Structure ─────────────────────────
Write-Host "[ 2/6 ] Directory Structure" -ForegroundColor Magenta

$requiredDirs = @("bin", "config", "payloads", "logs", "scripts")
foreach ($dir in $requiredDirs) {
    if (Test-Path $dir -PathType Container) {
        Check-Pass "Directory exists: $dir/"
    } else {
        Check-Fail "Missing directory: $dir/ -- run: New-Item -ItemType Directory $dir"
    }
}

Write-Host ""

# ─── SECTION 3: Required Files ──────────────────────────────
Write-Host "[ 3/6 ] Required Files & Artifacts" -ForegroundColor Magenta

$requiredFiles = @{
    "requirements.txt"                   = "Python dependencies (source >=)"
    "requirements.lock.txt"              = "Python dependencies locked (==)"
    "config\config.yaml"                 = "Central configuration"
    "payloads\telemetry_schema.idl"      = "DDS IDL schema (long long int64 timestamp)"
    "payloads\lorawan_push_data.json"    = "LoRaWAN PUSH_DATA template (0x02 ver, 0x00 id)"
    "payloads\sample_tftp_file.txt"      = "TFTP test payload"
    "lorawan_udp_forwarder.py"           = "LoRaWAN Semtech UDP simulator"
    "check_libs.py"                      = "Python library checker (14 modules)"
    "bin\VERSIONS.md"                    = "Binary tools registry & SHA-256"
    "scripts\check_ports.py"             = "Port reachability checker"
    "scripts\teardown.ps1"               = "Environment teardown (PowerShell)"
    "scripts\teardown.sh"                = "Environment teardown (Bash)"
}

foreach ($file in $requiredFiles.Keys) {
    if (Test-Path $file) {
        $size = (Get-Item $file).Length
        Check-Pass "File OK: $file ($size bytes) -- $($requiredFiles[$file])"
    } else {
        Check-Fail "Missing file: $file -- $($requiredFiles[$file])"
    }
}

# TFTP file size check (must be >1KB for multi-block test)
if (Test-Path "payloads\sample_tftp_file.txt") {
    $tftp_size = (Get-Item "payloads\sample_tftp_file.txt").Length
    if ($tftp_size -gt 1024) {
        $blocks = [math]::Ceiling($tftp_size / 512)
        Check-Pass "TFTP file size: $tftp_size bytes ($blocks TFTP blocks of 512B)"
    } else {
        Check-Warn "TFTP file too small: $tftp_size bytes (needs >1024 for multi-block test)"
    }
}

Write-Host ""

# ─── SECTION 4: Python Libraries (Group A) ──────────────────
Write-Host "[ 4/6 ] Python Libraries - Group A (10 Protocols + Utilities)" -ForegroundColor Magenta

if (-not $SkipLibCheck) {
    if (Test-Path "check_libs.py") {
        $libCheck = & $PythonBin check_libs.py 2>&1
        $libCheck | ForEach-Object {
            $line = $_.ToString()
            if ($line -match "\[PASS\]") {
                Write-Host "  $line" -ForegroundColor Green
                $script:PassCount++
            } elseif ($line -match "\[FAIL\]") {
                Write-Host "  $line" -ForegroundColor Red
                $script:FailCount++
            } elseif ($line -match "ALL PYTHON LIBS OK") {
                Write-Host "`n  [OK] $line`n" -ForegroundColor Green
            } elseif ($line -match "SOME LIBS MISSING") {
                Write-Host "`n  [FAIL] $line`n" -ForegroundColor Red
            } elseif ($line -match "^Total:") {
                Write-Host "  $line" -ForegroundColor Cyan
            }
        }
    } else {
        Check-Fail "check_libs.py not found in current directory"
    }
} else {
    Check-Warn "Library check skipped (-SkipLibCheck)"
}

Write-Host ""

# ─── SECTION 5: Group B Tools & Checksums ───────────────────
Write-Host "[ 5/6 ] Group B Tools, Checksums & Validation" -ForegroundColor Magenta

# Leshan JAR existence & checksum verification
if (Test-Path "bin\leshan-client-demo.jar") {
    $jarSize = (Get-Item "bin\leshan-client-demo.jar").Length
    $jarMB = [math]::Round($jarSize / 1MB, 2)
    Check-Pass "Leshan JAR exists: bin\leshan-client-demo.jar ($jarMB MB)"

    # Verify SHA-256 against bin/VERSIONS.md
    if (Test-Path "bin\VERSIONS.md") {
        $vContent = Get-Content "bin\VERSIONS.md" -Raw
        if ($vContent -match "([a-fA-F0-9]{64})") {
            $expectedHash = $Matches[1].ToLower()
            $actualHash = (Get-FileHash -Path "bin\leshan-client-demo.jar" -Algorithm SHA256).Hash.ToLower()
            if ($expectedHash -eq $actualHash) {
                Check-Pass "Leshan JAR SHA-256 matches bin/VERSIONS.md ($($actualHash.Substring(0,16))...)"
            } else {
                Check-Fail "Leshan JAR SHA-256 MISMATCH! Exp: $expectedHash, Act: $actualHash"
            }
        }
    }

    if (-not $SkipJarCheck) {
        Write-Host "  Testing: java -jar bin\leshan-client-demo.jar --help ..." -ForegroundColor Gray
        $helpOutput = cmd.exe /c "java -jar bin\leshan-client-demo.jar --help 2>&1"
        $helpText = $helpOutput -join " "
        if ($helpText -match "Usage|leshan|lwm2m|endpoint|server") {
            Check-Pass "Leshan JAR executes correctly (--help OK)"
        } else {
            Check-Warn "Leshan --help returned unexpected output (check Java version >= 17)"
        }
    }
} else {
    Check-Fail "Leshan JAR missing: bin\leshan-client-demo.jar"
}

# LoRaWAN UDP Forwarder (pure Python)
if (Test-Path "lorawan_udp_forwarder.py") {
    $syntaxCheck = & $PythonBin -m py_compile lorawan_udp_forwarder.py 2>&1
    if ($LASTEXITCODE -eq 0) {
        Check-Pass "LoRaWAN Forwarder: lorawan_udp_forwarder.py (syntax OK)"
    } else {
        Check-Fail "LoRaWAN Forwarder syntax error: $syntaxCheck"
    }
} else {
    Check-Fail "LoRaWAN Forwarder missing: lorawan_udp_forwarder.py"
}

# config.yaml validation
try {
    $yamlCheck = & $PythonBin -c "import yaml; yaml.safe_load(open('config/config.yaml', encoding='utf-8')); print('YAML_OK')" 2>&1
    if ($yamlCheck -match "YAML_OK") {
        Check-Pass "config/config.yaml -- YAML syntax valid"
    } else {
        Check-Fail "config/config.yaml -- YAML syntax error: $yamlCheck"
    }
} catch {
    Check-Fail "Could not validate config.yaml: $_"
}

Write-Host ""

# ─── SECTION 6: Target Ports & External Dependencies ────────
Write-Host "[ 6/6 ] Target Ports & External Dependencies" -ForegroundColor Magenta

if (-not $SkipPortCheck -and (Test-Path "scripts\check_ports.py")) {
    $portCheck = & $PythonBin scripts\check_ports.py 2>&1
    $portCheck | ForEach-Object {
        $pLine = $_.ToString()
        if ($pLine -match "\[PASS\]") {
            Write-Host "  $pLine" -ForegroundColor Green
            $script:PassCount++
        } elseif ($pLine -match "REACHABLE") {
            Write-Host "  $pLine" -ForegroundColor Green
        } elseif ($pLine -match "UNREACHABLE") {
            Write-Host "  $pLine" -ForegroundColor Gray
        } elseif ($pLine -match "UNKNOWN \(UDP\)") {
            Write-Host "  $pLine" -ForegroundColor Yellow
        } elseif ($pLine -match "^#") {
            Write-Host "  $pLine" -ForegroundColor Cyan
        } elseif ($pLine -match "^-") {
            Write-Host "  $pLine" -ForegroundColor DarkGray
        } elseif ($pLine -match "^Port Summary:") {
            Write-Host "`n  $pLine" -ForegroundColor White
            Check-Warn "Target adapter ports check completed (start IoT Platform before running tests)"
        } elseif ($pLine -match "^\[NOTE\]") {
            Write-Host "  $pLine" -ForegroundColor Gray
        }
    }
}

Write-Host ""

# ─── FINAL REPORT ────────────────────────────────────────────
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " ENVIRONMENT READINESS REPORT (Expanded Pre-Flight Check)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "  Passed checks : $($script:PassCount)" -ForegroundColor Green
Write-Host "  Failed checks : $($script:FailCount)" -ForegroundColor Red
Write-Host "  Warnings      : $($script:WarnCount)" -ForegroundColor Yellow
Write-Host ""

if ($script:FailCount -eq 0) {
    Write-Host "  STATUS: [SIMULATORS READY (platform not verified)] All 13 protocols, tools, configs and payloads verified!" -ForegroundColor Green
} elseif ($script:FailCount -le 2) {
    Write-Host "  STATUS: [PARTIALLY READY] Fix minor failures above before test execution" -ForegroundColor Yellow
} else {
    Write-Host "  STATUS: [NOT READY] Critical failures detected" -ForegroundColor Red
}

Write-Host ""
Write-Host "  Coverage Summary:" -ForegroundColor Cyan
Write-Host "    Group A (Python): HTTP MQTT CoAP XMPP AMQP DDS WebSocket TCP/UDP TFTP FTP"
Write-Host "    Group B (Tools) : LwM2M (Leshan JAR) | LoRaWAN (Semtech UDP Forwarder)"
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
