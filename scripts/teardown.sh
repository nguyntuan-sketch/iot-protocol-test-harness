#!/usr/bin/env bash
# ===========================================================
# IoT Protocol Test Harness - Teardown & Cleanup Script
# File: scripts/teardown.sh
# Usage: ./scripts/teardown.sh
# ===========================================================

set -e

echo "============================================================"
echo " IoT Protocol Test Harness - Teardown & Environment Cleanup"
echo "============================================================"

# 1. Stop background Leshan Java client
echo "[ 1/4 ] Stopping Leshan Java LwM2M client..."
if pgrep -f "leshan-client-demo.jar" > /dev/null 2>&1; then
    pkill -f "leshan-client-demo.jar" || true
    echo "  -> Leshan Java process stopped."
else
    echo "  -> No running Leshan Java process found."
fi

# 2. Stop Python background simulators if any
echo "[ 2/4 ] Stopping LoRaWAN Python Forwarder..."
if pgrep -f "lorawan_udp_forwarder.py" > /dev/null 2>&1; then
    pkill -f "lorawan_udp_forwarder.py" || true
    echo "  -> LoRaWAN forwarder process stopped."
else
    echo "  -> No running LoRaWAN forwarder found."
fi

# 3. Docker Compose teardown (optional profile)
echo "[ 3/4 ] Checking Docker Compose services..."
if command -v docker > /dev/null 2>&1; then
    if [ -f "docker/docker-compose.yml" ]; then
        echo "  -> Running docker compose down for profile lorawan-sim..."
        docker compose -f docker/docker-compose.yml --profile lorawan-sim down -v --remove-orphans 2>/dev/null || true
    fi
else
    echo "  -> Docker not installed; skipping docker compose down."
fi

# 4. Clean temporary files and caches
echo "[ 4/4 ] Cleaning temporary test files and pycache..."
rm -f logs/leshan_*.log logs/lorawan_*.log logs/*.tmp logs/*.lock
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true

echo "============================================================"
echo " Teardown complete. Environment cleaned."
echo "============================================================"
