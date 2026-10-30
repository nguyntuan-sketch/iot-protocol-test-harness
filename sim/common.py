"""
Common utilities for IoT Protocol Simulators
File: sim/common.py
Provides standardized exit codes, result formatting, exception classification, and shared TCP pre-flight probe.
"""

import sys
import os
import socket
import time
import re
import logging
import json
import random
import string
import concurrent.futures
from typing import Any, Callable, Dict, List, Optional, Tuple

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



def load_merged_config(base_dir: Optional[str] = None) -> dict:
    """
    Loads central configuration with hierarchical overrides:
    1. config/config.yaml (base configuration)
    2. config/config.local.yaml (optional local overrides, git-ignored)
    Returns merged dictionary.
    """
    if base_dir is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config = {}
    cfg_base_path = os.path.join(base_dir, "config", "config.yaml")
    cfg_local_path = os.path.join(base_dir, "config", "config.local.yaml")

    try:
        import yaml
    except ImportError:
        yaml = None

    if yaml and os.path.isfile(cfg_base_path):
        try:
            with open(cfg_base_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        except Exception as e:
            sys.stderr.write(f"[WARN] Failed to read config.yaml: {e}\n")

    if yaml and os.path.isfile(cfg_local_path):
        try:
            with open(cfg_local_path, "r", encoding="utf-8") as f:
                local_cfg = yaml.safe_load(f) or {}

            def _deep_merge(target: dict, source: dict):
                for k, v in source.items():
                    if isinstance(v, dict) and k in target and isinstance(target[k], dict):
                        _deep_merge(target[k], v)
                    else:
                        target[k] = v

            _deep_merge(config, local_cfg)
        except Exception as e:
            sys.stderr.write(f"[WARN] Failed to read config.local.yaml: {e}\n")

    return config


def resolve_password_from_file(file_path: Optional[str]) -> Optional[str]:
    """
    Reads password/secret from a text file, stripping whitespace and newline characters.
    """
    if not file_path:
        return None
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip("\r\n\t ")
    except Exception as e:
        sys.stderr.write(f"[WARN] Failed to read credential from file '{file_path}': {e}\n")
        return None


def validate_http_header_name(key: str) -> bool:
    """
    Validates HTTP header field name against RFC 7230 / RFC 9110 token specification.
    Must not contain CR, LF, or control characters.
    """
    if not key or not isinstance(key, str):
        return False
    # RFC 7230 token = 1*tchar
    return bool(re.match(r"^[a-zA-Z0-9!#$%&'*+\-.^_`|~]+$", key))


def validate_http_header_value(value: str) -> bool:
    """
    Validates HTTP header field value.
    Must not contain CR, LF, or ASCII control characters (0-31, 127).
    """
    if value is None or not isinstance(value, str):
        return False
    for ch in value:
        code = ord(ch)
        if code in (10, 13) or code < 32 or code == 127:
            return False
    return True


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

        config = load_merged_config(base_dir)
        _SHARED_REDACTOR = Redactor(config=config)
    except Exception:
        _SHARED_REDACTOR = None
    return _SHARED_REDACTOR


def register_simulator_args(args: Any) -> None:
    """Dynamically registers CLI arguments containing credentials into the redaction pool."""
    r = get_shared_redactor()
    if r and hasattr(args, "__dict__"):
        for k, v in vars(args).items():
            if isinstance(v, str) and any(w in k.lower() for w in ("token", "password", "secret", "psk", "key", "header_value")):
                r.add_secret(v)
            elif isinstance(v, list) and k == "headers":
                for item in v:
                    if isinstance(item, str) and "=" in item:
                        _, val = item.split("=", 1)
                        r.add_secret(val.strip())


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


def load_payload_config(proto: str, base_dir: Optional[str] = None, logger: Optional[logging.Logger] = None) -> Optional[dict]:
    """
    Load device payload configuration from config/payloads/<proto>.json.
    Returns dict if found, or None with warning if missing.
    """
    if not base_dir:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg_file = os.path.join(base_dir, "config", "payloads", f"{proto}.json")
    if os.path.isfile(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            msg = f"[!] Cảnh báo: Lỗi đọc file {cfg_file}: {e}, dùng payload mặc định"
            if logger:
                logger.warning(msg)
            else:
                print(msg, file=sys.stderr)
            return None
    else:
        msg = f"[!] Cảnh báo: không tìm thấy config/payloads/{proto}.json, dùng payload mặc định"
        if logger:
            logger.warning(msg)
        else:
            print(msg, file=sys.stderr)
        return None


def generate_payload(
    attributes: list,
    device_id: Optional[str] = None,
    seq: Optional[int] = None,
    logger: Optional[logging.Logger] = None,
) -> dict:
    """
    Generate dynamic payload according to attributes schema:
    - value is list -> random.choice(value), ignoring min/max
    - value is not None and not list -> value verbatim
    - value is None:
        - double/float -> random in [min(a,b), max(a,b)] with round(..., 2)
        - long/int -> random integer in [min(a,b), max(a,b)]
        - string -> random alphanumeric string of length in [min(a,b), max(a,b)]
        - bool -> random choice [True, False]
    Normalizes min/max order via min()/max() and logs normalization if min > max.
    """
    payload = {}
    seen_fields: Dict[str, str] = {}
    for attr in attributes:
        fname = attr.get("fieldName")
        if not fname:
            continue
        atype = str(attr.get("type", "string")).lower()
        if fname in seen_fields:
            prev_type = seen_fields[fname]
            warn_msg = f"[!] Cảnh báo: fieldName '{fname}' bị trùng lặp trong attributes (type trước: '{prev_type}', type sau: '{atype}'), giá trị sau sẽ ghi đè giá trị trước"
            if logger:
                logger.warning(warn_msg)
            else:
                print(warn_msg, flush=True)
        seen_fields[fname] = atype

        val = attr.get("value")

        if isinstance(val, list):
            payload[fname] = random.choice(val)
        elif val is not None:
            payload[fname] = val
        else:
            raw_min = attr.get("min")
            raw_max = attr.get("max")

            if atype in ("double", "float"):
                a = float(raw_min) if raw_min is not None else 0.0
                b = float(raw_max) if raw_max is not None else (a + 50.0 if raw_min is not None else 100.0)
                norm_min, norm_max = min(a, b), max(a, b)
                if a > b:
                    log_msg = f"[*] Chuẩn hóa khoảng giá trị cho '{fname}': [{a}, {b}] -> [{norm_min}, {norm_max}]"
                    if logger:
                        logger.info(log_msg)
                    else:
                        print(log_msg)
                payload[fname] = round(random.uniform(norm_min, norm_max), 2)

            elif atype in ("long", "int", "integer"):
                a = int(raw_min) if raw_min is not None else 0
                b = int(raw_max) if raw_max is not None else (a + 100 if raw_min is not None else 100)
                norm_min, norm_max = min(a, b), max(a, b)
                if a > b:
                    log_msg = f"[*] Chuẩn hóa khoảng giá trị cho '{fname}': [{a}, {b}] -> [{norm_min}, {norm_max}]"
                    if logger:
                        logger.info(log_msg)
                    else:
                        print(log_msg)
                payload[fname] = random.randint(norm_min, norm_max)

            elif atype in ("string", "str"):
                a = int(raw_min) if raw_min is not None else 6
                b = int(raw_max) if raw_max is not None else 10
                norm_min, norm_max = max(1, min(a, b)), max(1, max(a, b))
                if a > b:
                    log_msg = f"[*] Chuẩn hóa khoảng giá trị cho '{fname}': [{a}, {b}] -> [{norm_min}, {norm_max}]"
                    if logger:
                        logger.info(log_msg)
                    else:
                        print(log_msg)
                str_len = random.randint(norm_min, norm_max)
                chars = string.ascii_letters + string.digits
                payload[fname] = "".join(random.choice(chars) for _ in range(str_len))

            elif atype in ("bool", "boolean"):
                payload[fname] = random.choice([True, False])
            else:
                payload[fname] = str(val) if val is not None else ""

    if device_id is not None and "device_id" not in payload:
        payload["device_id"] = device_id
    if seq is not None and "seq" not in payload:
        payload["seq"] = seq
    if "timestamp" not in payload:
        payload["timestamp"] = int(time.time() * 1000)

    return payload


def setup_device_logger(proto: str, device_id: str, args: Any = None) -> logging.Logger:
    """
    Creates an isolated logger for a specific device thread, writing to:
    logs/<proto>_<8_first_chars_of_device_id>.log
    Every line prefixed with [device:<id>].
    """
    if args is not None:
        register_simulator_args(args)

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)

    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", str(device_id))[:8]
    log_filename = f"{proto}_{safe_id}.log"
    log_file = os.path.join(log_dir, log_filename)

    logger_name = f"{proto}_sim_{device_id}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)

    redactor = get_shared_redactor()
    redacting_filter = RedactingFilter(redactor=redactor)
    logger.addFilter(redacting_filter)

    # Avoid duplicate handlers if setup_device_logger called multiple times
    if not logger.handlers:
        file_formatter = RedactingFormatter(
            f"[device:{device_id}] %(asctime)s [%(levelname)s] %(message)s",
            redactor=redactor
        )
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(file_formatter)
        fh.addFilter(redacting_filter)
        logger.addHandler(fh)

        stream_formatter = RedactingFormatter(
            f"[device:{device_id}] %(message)s",
            redactor=redactor
        )
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        ch.setFormatter(stream_formatter)
        ch.addFilter(redacting_filter)
        logger.addHandler(ch)

    return logger


def run_devices_parallel(
    proto: str,
    mode: str,
    device_ids: List[str],
    worker_fn: Callable[[str, logging.Logger], Tuple[str, str]],
    timeout_per_device: Optional[float] = None,
    args: Any = None,
    logger: Optional[logging.Logger] = None,
) -> int:
    """
    Executes worker_fn in parallel for each device_id on dedicated threads.
    Aggregates results using strict priority:
    AUTH_FAILED > REFUSED > TIMEOUT > PROTO_ERROR > OK
    Formats single RESULT line and returns integer exit code.
    """
    if not device_ids:
        device_ids = ["sim_device_001"]

    if len(device_ids) > 50:
        warning_msg = "[!] Cảnh báo: số lượng thiết bị lớn có thể gây quá tải adapter khi test"
        if logger:
            logger.warning(warning_msg)
        else:
            print(warning_msg, flush=True)

    results: Dict[str, Tuple[str, str]] = {}
    max_workers = min(len(device_ids), 50)

    def _safe_worker(d_id: str) -> Tuple[str, str, str]:
        dev_log = setup_device_logger(proto, d_id, args=args)
        try:
            st, summary = worker_fn(d_id, dev_log)
            return d_id, st, summary
        except Exception as exc:
            if dev_log:
                dev_log.error(f"[!] Device worker exception: {exc}")
            return d_id, STATUS_PROTO_ERROR, f"PROTO_ERROR ({type(exc).__name__})"

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(_safe_worker, d_id): d_id for d_id in device_ids}
        wait_timeout = (timeout_per_device * 1.5) if timeout_per_device else None
        done, not_done = concurrent.futures.wait(future_map.keys(), timeout=wait_timeout)

        for fut in done:
            d_id = future_map[fut]
            try:
                _, st, summary = fut.result()
                results[d_id] = (st, summary)
            except Exception as exc:
                results[d_id] = (STATUS_PROTO_ERROR, f"PROTO_ERROR ({type(exc).__name__})")

        for fut in not_done:
            d_id = future_map[fut]
            results[d_id] = (STATUS_TIMEOUT, "TIMEOUT (thread timed out)")

    # Status priority: AUTH_FAILED > REFUSED > TIMEOUT > PROTO_ERROR > OK
    STATUS_PRIORITY = [STATUS_AUTH_FAILED, STATUS_REFUSED, STATUS_TIMEOUT, STATUS_PROTO_ERROR, STATUS_OK]
    overall_status = STATUS_OK
    for p in STATUS_PRIORITY:
        if any(res[0] == p for res in results.values()):
            overall_status = p
            break

    detail = ", ".join(f"{d_id}: {results[d_id][1]}" for d_id in device_ids if d_id in results)
    return format_result(proto, mode, overall_status, detail)


