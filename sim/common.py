"""
Common utilities for IoT Protocol Simulators
File: sim/common.py
Provides standardized exit codes, result formatting, exception classification, and shared TCP pre-flight probe.
"""

import sys
import os
import socket
import time

# Ensure console supports UTF-8 on Windows
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Standardized Exit Codes
EXIT_OK = 0
EXIT_PROTO_ERROR = 1
EXIT_REFUSED = 2
EXIT_TIMEOUT = 3
EXIT_AUTH_FAILED = 4

STATUS_OK = "OK"
STATUS_REFUSED = "REFUSED"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_AUTH_FAILED = "AUTH_FAILED"
STATUS_PROTO_ERROR = "PROTO_ERROR"


def format_result(proto: str, mode: str, status: str, detail: str, stage: str = None, elapsed: float = None) -> int:
    """
    Format and print the mandatory standardized output line:
    RESULT: <proto> <mode>=<OK|REFUSED|TIMEOUT|AUTH_FAILED|PROTO_ERROR> detail=<stage: ...>
    If elapsed is provided and status is REFUSED or TIMEOUT, appends 'elapsed=<sec>s'.
    Returns the corresponding integer exit code.
    """
    detail_str = str(detail).strip()
    if not detail_str:
        detail_str = "No further detail provided"

    # Avoid duplicate [stage:...] prefixes
    if stage and not detail_str.startswith("[stage:"):
        full_detail = f"[stage:{stage}] {detail_str}"
    else:
        full_detail = detail_str

    if elapsed is not None and "elapsed=" not in full_detail:
        if status in (STATUS_REFUSED, STATUS_TIMEOUT) or "tcp_connect" in full_detail:
            full_detail = f"{full_detail} elapsed={elapsed:.2f}s"

    line = f"RESULT: {proto} {mode}={status} detail={full_detail}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", errors="replace").decode("ascii"), flush=True)

    status_map = {
        STATUS_OK: EXIT_OK,
        STATUS_REFUSED: EXIT_REFUSED,
        STATUS_TIMEOUT: EXIT_TIMEOUT,
        STATUS_AUTH_FAILED: EXIT_AUTH_FAILED,
        STATUS_PROTO_ERROR: EXIT_PROTO_ERROR,
    }
    return status_map.get(status, EXIT_PROTO_ERROR)


def classify_socket_error(err: Exception, stage: str = "tcp_connect", elapsed: float = None) -> tuple[str, str, int]:
    """
    Classify low-level socket / OS exceptions into (status, detail, exit_code).
    Inspects str, repr, and nested args.
    Appends elapsed=<sec>s if elapsed is provided.
    """
    err_str = str(err)
    err_repr = repr(err)
    args_str = " ".join(repr(a) for a in getattr(err, "args", []))
    combined = f"{err_str} {err_repr} {args_str}".lower()

    elapsed_suffix = f" elapsed={elapsed:.2f}s" if (elapsed is not None and "elapsed=" not in combined) else ""

    # WinError 10061: Connection refused (TCP RST)
    # ConnectionRefusedError: Linux/POSIX ECONNREFUSED
    if "10061" in combined or "connection refused" in combined or "econnrefused" in combined or "actively refused" in combined:
        return STATUS_REFUSED, f"[stage:{stage}] {err_str or err_repr}{elapsed_suffix}", EXIT_REFUSED

    # WinError 10054: Connection reset by peer / ICMP Port Unreachable (UDP)
    # WinError 1234: No service is operating at the destination network endpoint
    if "10054" in combined or "1234" in combined or "connection reset" in combined or "port unreachable" in combined:
        return STATUS_REFUSED, f"[stage:{stage}] {err_str or err_repr} (ICMP Port Unreachable / Connection Reset){elapsed_suffix}", EXIT_REFUSED

    # Timeouts
    if "timed out" in combined or "timeout" in combined or "10060" in combined or "etimedout" in combined:
        return STATUS_TIMEOUT, f"[stage:{stage}] {err_str or err_repr}{elapsed_suffix}", EXIT_TIMEOUT

    return STATUS_PROTO_ERROR, f"[stage:{stage}] {err_str or err_repr}{elapsed_suffix}", EXIT_PROTO_ERROR


def tcp_preflight_probe(host: str, port: int, timeout: float = 3.0) -> tuple[bool, str, str, float]:
    """
    Perform a unified pre-flight TCP connect probe across all TCP-based simulators.
    Returns:
        (is_connected, status, detail, elapsed_time)
    """
    t0 = time.perf_counter()
    sock = None
    try:
        sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        elapsed = time.perf_counter() - t0
        return True, STATUS_OK, "TCP socket connected", elapsed
    except (ConnectionRefusedError, OSError) as e:
        elapsed = time.perf_counter() - t0
        status, detail, code = classify_socket_error(e, stage="tcp_connect", elapsed=elapsed)
        return False, status, detail, elapsed
    except Exception as e:
        elapsed = time.perf_counter() - t0
        status, detail, code = classify_socket_error(e, stage="tcp_connect", elapsed=elapsed)
        return False, status, detail, elapsed
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass
