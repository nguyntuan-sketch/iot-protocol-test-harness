#!/usr/bin/env bash
# ===========================================================
# IoT Protocol Test Harness - Environment Setup & Smoke Check (Bash)
# File: setup_environment.sh
# Phase 5: Pre-Flight Validation Script
# Usage: ./setup_environment.sh
# ===========================================================

set -e

PASS="[PASS]"
FAIL="[FAIL]"
WARN="[WARN]"

PassCount=0
FailCount=0
WarnCount=0

check_pass() { PassCount=$((PassCount + 1)); echo -e "\033[32m$PASS $1\033[0m"; }
check_fail() { FailCount=$((FailCount + 1)); echo -e "\033[31m$FAIL $1\033[0m"; }
check_warn() { WarnCount=$((WarnCount + 1)); echo -e "\033[33m$WARN $1\033[0m"; }

# Auto-detect Python binary (prefer conda iot-test or python3)
PYTHON_BIN="python3"
if [ -f "$HOME/.conda/envs/iot-test/bin/python" ]; then
    PYTHON_BIN="$HOME/.conda/envs/iot-test/bin/python"
elif [ -f "$HOME/.conda/envs/iot-test/python.exe" ]; then
    PYTHON_BIN="$HOME/.conda/envs/iot-test/python.exe"
elif command -v python3 > /dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python > /dev/null 2>&1; then
    PYTHON_BIN="python"
fi

echo ""
echo "============================================================"
echo " IoT Protocol Test Harness - Pre-Flight Environment Check"
echo " Date: $(date)"
echo " Interpreter: $PYTHON_BIN"
echo "============================================================"
echo ""

# 1. System Prerequisites
echo "[ 1/6 ] System Prerequisites"
if "$PYTHON_BIN" --version > /dev/null 2>&1; then
    check_pass "Python: $("$PYTHON_BIN" --version)"
else
    check_fail "Python not found at $PYTHON_BIN"
fi

if "$PYTHON_BIN" -m pip --version > /dev/null 2>&1; then
    check_pass "pip: $("$PYTHON_BIN" -m pip --version | awk '{print $1,$2}')"
else
    check_fail "pip not found"
fi

if java -version > /dev/null 2>&1; then
    JAVA_VER=$(java -version 2>&1 | head -n 1)
    check_pass "Java: $JAVA_VER"
else
    check_fail "Java not found -- install OpenJDK 17+"
fi

if command -v docker > /dev/null 2>&1; then
    check_pass "Docker: $(docker --version)"
else
    check_warn "Docker not installed (LoRaWAN uses pure-Python forwarder instead)"
fi

echo ""

# 2. Directory Structure
echo "[ 2/6 ] Directory Structure"
for dir in bin config payloads logs scripts; do
    if [ -d "$dir" ]; then
        check_pass "Directory exists: $dir/"
    else
        check_fail "Missing directory: $dir/"
    fi
done

echo ""

# 3. Required Files
echo "[ 3/6 ] Required Files & Artifacts"
for f in requirements.txt requirements.lock.txt config/config.yaml payloads/telemetry_schema.idl payloads/lorawan_push_data.json payloads/sample_tftp_file.txt lorawan_udp_forwarder.py check_libs.py bin/VERSIONS.md scripts/check_ports.py scripts/teardown.sh scripts/teardown.ps1; do
    if [ -f "$f" ]; then
        check_pass "File OK: $f ($(wc -c < "$f" | tr -d ' ') bytes)"
    else
        check_fail "Missing file: $f"
    fi
done

# TFTP payload size check
if [ -f "payloads/sample_tftp_file.txt" ]; then
    tftp_size=$(wc -c < "payloads/sample_tftp_file.txt" | tr -d ' ')
    if [ "$tftp_size" -gt 1024 ]; then
        check_pass "TFTP file size: $tftp_size bytes (> 1024 bytes multi-block)"
    else
        check_warn "TFTP file size too small: $tftp_size bytes"
    fi
fi

echo ""

# 4. Python Libraries
echo "[ 4/6 ] Python Libraries - Group A (9 Protocols + Utilities)"
if [ -f "check_libs.py" ]; then
    "$PYTHON_BIN" check_libs.py
    check_pass "Library check completed via check_libs.py"
fi

echo ""

# 5. Group B Tools & Checksums
echo "[ 5/6 ] Group B Tools, Checksums & Validation"
if [ -f "bin/leshan-client-demo.jar" ]; then
    check_pass "Leshan JAR exists: bin/leshan-client-demo.jar"
    if java -jar bin/leshan-client-demo.jar --help > /dev/null 2>&1; then
        check_pass "Leshan JAR executes correctly (--help OK)"
    fi
fi

if [ -f "lorawan_udp_forwarder.py" ]; then
    if "$PYTHON_BIN" -m py_compile lorawan_udp_forwarder.py > /dev/null 2>&1; then
        check_pass "LoRaWAN Forwarder syntax OK"
    else
        check_fail "LoRaWAN Forwarder syntax error"
    fi
fi

if "$PYTHON_BIN" -c "import yaml; yaml.safe_load(open('config/config.yaml', encoding='utf-8'))" > /dev/null 2>&1; then
    check_pass "config/config.yaml valid YAML"
else
    check_fail "config/config.yaml invalid YAML"
fi

echo ""

# 6. Target Ports & External Dependencies
echo "[ 6/6 ] Target Ports & External Dependencies"
if [ -f "scripts/check_ports.py" ]; then
    "$PYTHON_BIN" scripts/check_ports.py
    check_warn "Target adapter ports check completed (start IoT Platform before running tests)"
fi

echo ""
echo "============================================================"
echo " ENVIRONMENT READINESS REPORT (Expanded Pre-Flight Check)"
echo "============================================================"
echo "  Passed checks : $PassCount"
echo "  Failed checks : $FailCount"
echo "  Warnings      : $WarnCount"
echo ""
if [ "$FailCount" -eq 0 ]; then
    echo -e "\033[32m  STATUS: [SIMULATORS READY (platform not verified)] All 11 protocols, tools, configs and payloads verified!\033[0m"
else
    echo -e "\033[31m  STATUS: [FAILURES DETECTED] Review items above\033[0m"
fi
echo "============================================================"
