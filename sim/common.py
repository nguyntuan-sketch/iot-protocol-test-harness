"""
Common utilities for IoT Protocol Simulators
File: sim/common.py
Provides standardized exit codes, result formatting, exception classification, and shared TCP pre-flight probe.
"""

import sys
import os
import socket
import time
import logging
from typing import Any, Optional

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


# ─── Standardized Secure Logger Setup with Credential Redaction ───────────────

_SHARED_REDACTOR = None


def get_shared_redactor():
    """Returns singleton Redactor instance loaded with config.yaml secrets."""
    global _SHARED_REDACTOR
    if _SHARED_REDACTOR is not None:
        return _SHARED_REDACTOR
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if base_dir not in sys.path:
            sys.path.insert(0, base_dir)
        from runner.redact import Redactor

        config = None
        config_path = os.path.join(base_dir, "config", "config.yaml")
        if os.path.isfile(config_path):
            try:
                import yaml

                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
            except Exception:
                pass
        _SHARED_REDACTOR = Redactor(config=config)
    except Exception:
        _SHARED_REDACTOR = None
    return _SHARED_REDACTOR


def register_simulator_args(args: Any) -> None:
    """Dynamically registers CLI arguments containing credentials into the redaction pool."""
    r = get_shared_redactor()
    if r and hasattr(args, "__dict__"):
        for k, v in vars(args).items():
            if isinstance(v, str) and any(w in k.lower() for w in ("token", "password", "secret", "psk", "key")):
                r.add_secret(v)


class RedactingFilter(logging.Filter):
    """
    Logging filter that redacts credentials in all log messages (record.msg and record.args)
    before they are emitted to console or log files.
    """

    def __init__(self, redactor=None):
        super().__init__()
        self.redactor = redactor or get_shared_redactor()

    def filter(self, record: logging.LogRecord) -> bool:
        if self.redactor:
            try:
                if isinstance(record.msg, str):
                    record.msg = self.redactor.redact_text(record.msg)
                if record.args:
                    if isinstance(record.args, dict):
                        record.args = self.redactor.redact_data(record.args)
                    elif isinstance(record.args, (tuple, list)):
                        record.args = tuple(self.redactor.redact_data(a) for a in record.args)
                if record.exc_text:
                    record.exc_text = self.redactor.redact_text(record.exc_text)
            except Exception:
                pass
        return True


class RedactingFormatter(logging.Formatter):
    """
    Logging formatter that redacts credentials from the fully formatted message.
    """

    def __init__(self, fmt=None, datefmt=None, redactor=None):
        super().__init__(fmt, datefmt)
        self.redactor = redactor or get_shared_redactor()

    def format(self, record: logging.LogRecord) -> str:
        s = super().format(record)
        if self.redactor:
            s = self.redactor.redact_text(s)
        return s


def setup_simulator_logger(proto: str, log_filename: Optional[str] = None, args: Any = None) -> logging.Logger:
    """
    Standardized, secure logger setup for all simulators in sim/*.py.
    Applies RedactingFilter and RedactingFormatter so that NO credentials appear
    in logs/<proto>.log or stdout.
    """
    if args is not None:
        register_simulator_args(args)

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    if not log_filename:
        log_filename = f"{proto}.log"
    log_file = os.path.join(log_dir, log_filename)

    logger = logging.getLogger(f"{proto}_sim")
    logger.setLevel(logging.DEBUG)

    redactor = get_shared_redactor()
    redacting_filter = RedactingFilter(redactor=redactor)
    logger.addFilter(redacting_filter)

    # Avoid duplicate handlers if setup_logger called multiple times
    if not logger.handlers:
        file_formatter = RedactingFormatter("%(asctime)s [%(levelname)s] %(message)s", redactor=redactor)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(file_formatter)
        fh.addFilter(redacting_filter)
        logger.addHandler(fh)

        stream_formatter = RedactingFormatter("%(message)s", redactor=redactor)
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        ch.setFormatter(stream_formatter)
        ch.addFilter(redacting_filter)
        logger.addHandler(ch)

    return logger

