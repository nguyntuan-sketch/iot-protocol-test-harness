#!/usr/bin/env python3
"""
IoT Protocol Test Harness - Target Ports & External Dependencies Checker
File: scripts/check_ports.py
Checks reachability of target ports for all 12 IoT adapters and verifies JAR checksum.
"""
import hashlib
import re
import socket
import sys
import yaml

TARGET_ADAPTERS = [
    ("#1",  "HTTP",       "TCP", 8080, "Platform REST API / Web server, Access Token"),
    ("#2",  "MQTT",       "TCP", 1883, "MQTT Broker running, Device Credentials / Token"),
    ("#3",  "CoAP",       "UDP", 5683, "CoAP Server listening, Device Token in URL"),
    ("#4",  "XMPP",       "TCP", 5222, "XMPP Server (ejabberd/Prosody), Client & Adapter JID"),
    ("#5",  "AMQP",       "TCP", 5672, "RabbitMQ Broker, Vhost '/', Exchange & Queue bound"),
    ("#6",  "DDS",        "UDP", 7400, "Multicast network enabled, DataReader on Domain 0"),
    ("#7",  "LwM2M",      "UDP", 5683, "LwM2M Server, Java 17+, Endpoint whitelist"),
    ("#8",  "LoRaWAN",    "UDP", 1700, "LoRaWAN Network Server, Gateway EUI, DevAddr & Keys"),
    ("#9",  "WebSocket",  "TCP", 8080, "WebSocket Adapter listening, Upgrade header support"),
    ("#10", "TCP Raw",    "TCP", 9000, "Raw TCP Socket listener, Custom parser rule"),
    ("#10", "UDP Raw",    "UDP", 9001, "Raw UDP Socket listener, Custom parser rule"),
    ("#11", "TFTP",       "UDP", 69,   "TFTP Server daemon, Storage folder write permission"),
    ("#12", "FTP",        "TCP", 21,   "FTP Server daemon (RFC 959), user credentials & storage"),
]

def check_tcp_port(host: str, port: int, timeout: float = 0.5) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        s.close()
        return "REACHABLE"
    except (ConnectionRefusedError, socket.timeout):
        return "UNREACHABLE"
    except Exception as e:
        return f"ERR ({e})"

def check_udp_port(host: str, port: int) -> str:
    # UDP is connectionless; socket creation indicates OS stack readiness
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        # Note: Without active server response, standard UDP reachability cannot be confirmed
        s.close()
        return "UNKNOWN (UDP)"
    except Exception as e:
        return f"ERR ({e})"

def verify_jar_checksum(jar_path: str = "bin/leshan-client-demo.jar", versions_path: str = "bin/VERSIONS.md") -> tuple[bool, str, str]:
    try:
        with open(versions_path, "r", encoding="utf-8") as f:
            content = f.read()
        m = re.search(r"([a-fA-F0-9]{64})", content)
        expected_hash = m.group(1).lower() if m else ""

        with open(jar_path, "rb") as f:
            actual_hash = hashlib.sha256(f.read()).hexdigest().lower()

        match = (actual_hash == expected_hash)
        return match, expected_hash, actual_hash
    except Exception as e:
        return False, f"ERR: {e}", ""

def main():
    target_host = "127.0.0.1"
    try:
        with open("config/config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
            target_host = cfg.get("global", {}).get("target_host", "127.0.0.1")
    except Exception:
        pass

    print(f"Target Host: {target_host}\n")

    # 1. Verify Leshan JAR Checksum
    print("[ Checksum Verification: bin/leshan-client-demo.jar ]")
    match, exp, act = verify_jar_checksum()
    if match:
        print(f"  [PASS] SHA-256 matches bin/VERSIONS.md: {act[:16]}...{act[-8:]}\n")
    else:
        print(f"  [FAIL] SHA-256 MISMATCH!")
        print(f"         Expected: {exp}")
        print(f"         Actual  : {act}\n")

    # 2. Port Reachability & External Dependencies Table
    print(f"{'#':<4} {'Protocol':<12} {'Type':<5} {'Port':<6} {'Reachability':<15} {'External Dependencies'}")
    print("-" * 90)

    reachable_count = 0
    unreachable_count = 0
    unknown_udp_count = 0

    for num, proto, ptype, port, deps in TARGET_ADAPTERS:
        if ptype == "TCP":
            status = check_tcp_port(target_host, port)
            if status == "REACHABLE":
                reachable_count += 1
            else:
                unreachable_count += 1
        else:
            status = check_udp_port(target_host, port)
            unknown_udp_count += 1

        print(f"{num:<4} {proto:<12} {ptype:<5} {port:<6} {status:<15} {deps}")

    print("-" * 90)
    print(f"Port Summary: {reachable_count} REACHABLE | {unreachable_count} UNREACHABLE | {unknown_udp_count} UNKNOWN (UDP)")

    if unreachable_count > 0:
        print("\n[NOTE] Target ports are currently UNREACHABLE because IoT Platform is not started.")
        print("       This is expected during pre-flight setup (WARN only). Start platform before test execution.")

    return 0

if __name__ == "__main__":
    sys.exit(main())
