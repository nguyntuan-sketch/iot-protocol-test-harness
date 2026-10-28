#!/usr/bin/env python3
"""
IoT Protocol Test Harness - Python Library Verification
Checks all dependencies required for the 11 IoT protocols and testing utilities.
"""
import importlib
import importlib.metadata
import sys

LIBS = [
    ("requests", "Protocol #1  HTTP"),
    ("paho.mqtt.client", "Protocol #2  MQTT"),
    ("aiocoap", "Protocol #3  CoAP"),
    ("slixmpp", "Protocol #4  XMPP"),
    ("pika", "Protocol #5  AMQP"),
    ("cyclonedds", "Protocol #6  DDS (Cyclone DDS)"),
    ("websockets", "Protocol #9  WebSocket"),
    ("tftpy", "Protocol #11 TFTP"),
    ("yaml", "Config loader (PyYAML)"),
    ("pytest", "Test runner"),
    ("pytest_asyncio", "Async test runner"),
    ("socket", "Protocol #10 TCP/UDP (stdlib)"),
    ("struct", "LoRaWAN packet builder (stdlib)"),
    ("base64", "LoRaWAN payload encoder (stdlib)"),
]

def main():
    print(f"Python interpreter: {sys.executable}")
    print(f"Python version    : {sys.version.split()[0]}\n")
    print(f"{'Status':<8} {'Library / Module':<25} {'Protocol / Role':<32} {'Version'}")
    print("-" * 80)

    all_ok = True
    passed = 0
    failed = 0

    for mod_name, desc in LIBS:
        try:
            mod = importlib.import_module(mod_name)
            ver = ""
            for attr in ("__version__", "version", "VERSION"):
                if hasattr(mod, attr):
                    val = getattr(mod, attr)
                    if isinstance(val, (str, int, float, tuple)):
                        ver = str(val)
                        break
            if not ver:
                try:
                    pkg_name = mod_name.split(".")[0]
                    ver = importlib.metadata.version(pkg_name)
                except Exception:
                    ver = "detected (built-in/no version)"
            print(f"[PASS]   {mod_name:<25} {desc:<32} {ver}")
            passed += 1
        except Exception as e:
            print(f"[FAIL]   {mod_name:<25} {desc:<32} ERR: {e}")
            all_ok = False
            failed += 1

    print("-" * 80)
    print(f"Total: {passed + failed} | Passed: {passed} | Failed: {failed}")
    if all_ok:
        print("\nALL PYTHON LIBS OK")
        return 0
    else:
        print("\nSOME LIBS MISSING - please check failed items above")
        return 1

if __name__ == "__main__":
    sys.exit(main())
