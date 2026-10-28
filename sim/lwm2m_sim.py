#!/usr/bin/env python3
"""
LwM2M IoT Protocol Simulator CLI
Protocol #7: OMA LwM2M Client (Eclipse Leshan 2.0.0-M17 Java Wrapper)
Usage:
  python sim/lwm2m_sim.py --mode connect --timeout 5
  python sim/lwm2m_sim.py --mode connect --tls --psk-id my_id --psk-key 0123456789abcdef
  python sim/lwm2m_sim.py --mode send --duration 10
  python sim/lwm2m_sim.py --mode listen --duration 15
  python sim/lwm2m_sim.py --mode both --duration 12
"""

import os
import sys
import time
import logging
import argparse
import subprocess
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)

from sim.common import (
    EXIT_OK, EXIT_PROTO_ERROR, EXIT_REFUSED, EXIT_TIMEOUT, EXIT_AUTH_FAILED,
    STATUS_OK, STATUS_REFUSED, STATUS_TIMEOUT, STATUS_AUTH_FAILED, STATUS_PROTO_ERROR,
    format_result
)

try:
    import yaml
except ImportError:
    yaml = None


def get_default_config():
    cfg_path = os.path.join(base_dir, "config", "config.yaml")
    cfg = {
        "host": "127.0.0.1",
        "port": 5683,
        "port_tls": 5684,
        "server_uri": "coap://127.0.0.1:5683",
        "endpoint_name": "lwm2m_sim_device_001",
        "jar_path": os.path.join(base_dir, "bin", "leshan-client-demo.jar"),
        "lifetime_sec": 60,
        "tls_enabled": False,
        "psk_identity": None,
        "psk_key": None
    }
    if yaml and os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
                target = raw.get("target", {})
                lw = raw.get("adapters", {}).get("lwm2m", {})
                tls = raw.get("tls", {})
                cfg["host"] = target.get("host", cfg["host"])
                cfg["port"] = lw.get("port", cfg["port"])
                cfg["server_uri"] = lw.get("server_uri", cfg["server_uri"])
                cfg["endpoint_name"] = lw.get("endpoint_name", cfg["endpoint_name"])
                cfg["tls_enabled"] = tls.get("enabled", cfg["tls_enabled"])
                ports = tls.get("ports", {})
                cfg["port_tls"] = ports.get("lwm2m_dtls", cfg["port_tls"])
                if "jar_path" in lw:
                    cfg["jar_path"] = os.path.join(base_dir, lw["jar_path"])
        except Exception as e:
            sys.stderr.write(f"[WARN] Failed to read config.yaml: {e}\n")
    return cfg


def setup_logger():
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "lwm2m.log")

    logger = logging.getLogger("lwm2m_sim")
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)

    return logger


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryLimit", ctypes.c_size_t),
            ("PeakJobMemoryLimit", ctypes.c_size_t),
        ]

    def _create_lwm2m_job():
        """Creates a Windows Job Object with KILL_ON_CLOSE to prevent orphaned JVM processes."""
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            hJob = kernel32.CreateJobObjectW(None, None)
            if not hJob:
                return None
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            JobObjectExtendedLimitInformation = 9
            kernel32.SetInformationJobObject(
                hJob,
                JobObjectExtendedLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            return hJob
        except Exception:
            return None
else:
    def _create_lwm2m_job():
        return None


def find_real_java_executable(logger=None) -> str:
    """
    Locates direct JDK java.exe to avoid Oracle's javapath shim on Windows.
    Order of preference:
    1. JAVA_HOME environment variable
    2. C:\\Program Files\\Java\\jdk-* / latest
    3. Fallback to 'java' on PATH (with warning)
    """
    java_home = os.environ.get("JAVA_HOME")
    if java_home and os.path.isdir(java_home):
        cand = os.path.join(java_home, "bin", "java.exe" if sys.platform == "win32" else "java")
        if os.path.isfile(cand):
            return cand

    if sys.platform == "win32":
        java_root = r"C:\Program Files\Java"
        if os.path.isdir(java_root):
            try:
                entries = os.listdir(java_root)
                jdk_dirs = [d for d in entries if d.lower().startswith("jdk-") or d.lower() == "latest"]
                jdk_dirs.sort(reverse=True)
                for d in jdk_dirs:
                    cand = os.path.join(java_root, d, "bin", "java.exe")
                    if os.path.isfile(cand):
                        return cand
                for d in entries:
                    cand = os.path.join(java_root, d, "bin", "java.exe")
                    if os.path.isfile(cand):
                        return cand
            except Exception:
                pass

    if logger:
        logger.warning("[!] Cảnh báo: Không tìm thấy JDK trực tiếp dưới JAVA_HOME hoặc C:\\Program Files\\Java. Dùng 'java' trên PATH (có thể qua Oracle shim).")
    return "java"


def cleanup_process(proc, logger, hJob=None):
    """Ensure child Java process and its process tree are terminated and not left orphan."""
    if proc and proc.poll() is None:
        logger.debug(f"[*] Terminating Leshan Java process PID {proc.pid}...")
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                               capture_output=True, text=True, timeout=5)
            proc.kill()
            proc.wait(timeout=2.0)
            logger.debug(f"[*] Java process PID {proc.pid} exited.")
        except Exception as e:
            logger.warning(f"[!] Warning terminating process: {e}")
    if hJob and sys.platform == "win32":
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle(hJob)
        except Exception:
            pass


def main():
    cfg = get_default_config()
    parser = argparse.ArgumentParser(
        description="LwM2M IoT Protocol Simulator CLI (Leshan Client Demo Wrapper)",
        epilog="Chế độ 'connect' khởi chạy client và chờ xác nhận Registration từ server. Hỗ trợ --tls (coaps DTLS với -i/-p)."
    )
    parser.add_argument("--mode", choices=["connect", "send", "listen", "both"], default="connect",
                        help="Operating mode: 'connect' (wait for registration), 'send', 'listen', or 'both' (default: connect)")
    parser.add_argument("--server-uri", default=None, help="LwM2M Server URI (e.g. coap://127.0.0.1:5683 or coaps://127.0.0.1:5684)")
    parser.add_argument("--endpoint", default=cfg["endpoint_name"], help=f"LwM2M Endpoint Name (default: {cfg['endpoint_name']})")
    parser.add_argument("--jar", default=cfg["jar_path"], help=f"Path to leshan-client-demo.jar (default: {cfg['jar_path']})")
    parser.add_argument("--tls", action="store_true", default=cfg.get("tls_enabled", False), help="Use coaps:// DTLS")
    parser.add_argument("--psk-id", default=cfg.get("psk_identity"), help="PSK Identity for DTLS (-i)")
    parser.add_argument("--psk-key", default=cfg.get("psk_key"), help="Pre-shared Key in hex for DTLS (-p)")
    parser.add_argument("--duration", type=float, default=8.0, help="Execution duration in seconds (default: 8.0)")
    parser.add_argument("--timeout", type=float, default=5.0, help="Registration timeout in seconds (default: 5.0)")

    args = parser.parse_args()
    logger = setup_logger()

    if not os.path.isfile(args.jar):
        logger.error(f"[!] Leshan JAR file not found at: {args.jar}")
        return format_result("lwm2m", args.mode, STATUS_PROTO_ERROR, f"Leshan JAR not found at {args.jar}", stage="init")

    if args.server_uri is None:
        if args.tls:
            server_uri = f"coaps://{cfg['host']}:{cfg['port_tls']}"
        else:
            server_uri = cfg["server_uri"]
    else:
        server_uri = args.server_uri

    java_bin = find_real_java_executable(logger)
    cmd = [java_bin, "-jar", args.jar, "-u", server_uri, "-n", args.endpoint]
    if args.tls:
        if args.psk_id and args.psk_key:
            cmd.extend(["-i", args.psk_id, "-p", args.psk_key])
        else:
            logger.info("    [!] DTLS enabled without PSK credentials (-i, -p). Note: Leshan requires credentials for coaps://.")

    logger.info(f"[*] Starting LwM2M Simulator in '{args.mode}' mode (DTLS: {args.tls})...")
    logger.info(f"    Command: {' '.join(cmd)}")
    logger.info(f"    Target Server: {server_uri} | Endpoint: {args.endpoint}")

    temp_log = os.path.join(base_dir, "logs", "lwm2m_runtime_raw.log")
    exec_duration = args.timeout if args.mode == "connect" else args.duration

    hJob = _create_lwm2m_job()
    proc = None
    try:
        with open(temp_log, "w", encoding="utf-8") as f:
            proc = subprocess.Popen(
                cmd,
                stdout=f,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL
            )
            if hJob and sys.platform == "win32":
                try:
                    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                    kernel32.AssignProcessToJobObject(hJob, proc._handle)
                except Exception:
                    pass
            logger.info(f"[*] Spawned Leshan Java PID: {proc.pid}")

            t0 = time.time()
            reg_found = False
            while time.time() - t0 < exec_duration:
                # Check raw log dynamically
                if os.path.isfile(temp_log):
                    with open(temp_log, "r", encoding="utf-8", errors="replace") as rf:
                        content = rf.read()
                        if "Registered with location" in content:
                            reg_found = True
                            break
                        if "4.01" in content or "4.03" in content:
                            break
                time.sleep(0.3)
    except Exception as e:
        logger.error(f"[!] Failed to execute Leshan client: {e}")
        return format_result("lwm2m", args.mode, STATUS_PROTO_ERROR, f"Execution failed: {e}", stage="init")
    finally:
        cleanup_process(proc, logger, hJob)

    raw_output = ""
    if os.path.isfile(temp_log):
        with open(temp_log, "r", encoding="utf-8", errors="replace") as f:
            raw_output = f.read()

    logger.info("=== [LESHAN RUNTIME RAW LOG START] ===")
    for line in raw_output.splitlines():
        if line.strip():
            logger.info(f"  {line}")
    logger.info("=== [LESHAN RUNTIME RAW LOG END] ===")

    # Analysis of real observed Leshan logs
    if "Registered with location" in raw_output:
        loc = "Registered with location"
        for line in raw_output.splitlines():
            if "Registered with location" in line:
                loc = line.strip()
                break
        logger.info(f"[*] LwM2M Client successfully registered: {loc}")
        detail = loc
        if args.mode in ["listen", "both"]:
            detail = f"{loc}, Recv:0 (no downlink received)"
        return format_result("lwm2m", args.mode, STATUS_OK, detail, stage="registration")
    elif "4.01" in raw_output or "4.03" in raw_output or "Unauthorized" in raw_output:
        logger.error(f"[!] LwM2M Registration rejected by server (4.01/4.03 Unauthorized)")
        return format_result("lwm2m", args.mode, STATUS_AUTH_FAILED, "Registration rejected (4.01/4.03 Unauthorized)", stage="auth")
    elif "Trying to register to" in raw_output:
        logger.error(f"[!] LwM2M Registration timeout / not acknowledged by server at {server_uri}.")
        return format_result("lwm2m", args.mode, STATUS_TIMEOUT,
                             f"Registration timeout after {exec_duration}s (server {server_uri} silent)",
                             stage="registration")
    elif "Connection refused" in raw_output or "10061" in raw_output or "10054" in raw_output:
        logger.error(f"[!] LwM2M connection refused at network level: {server_uri}")
        return format_result("lwm2m", args.mode, STATUS_REFUSED, f"Network refused connection to {server_uri}", stage="transport")
    else:
        logger.error(f"[!] LwM2M client failed or returned unknown response.")
        first_line = raw_output.splitlines()[0] if raw_output.splitlines() else "No output from client"
        return format_result("lwm2m", args.mode, STATUS_PROTO_ERROR, first_line, stage="transport")


if __name__ == "__main__":
    sys.exit(main())
