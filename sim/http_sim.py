#!/usr/bin/env python3
"""
HTTP IoT Protocol Simulator CLI
Protocol #1: HTTP REST Telemetry & Downlink Polling
Usage:
  python sim/http_sim.py --mode connect
  python sim/http_sim.py --mode connect --tls --insecure
  python sim/http_sim.py --mode send --count 3 --interval 1.0
  python sim/http_sim.py --mode listen --duration 5.0
  python sim/http_sim.py --mode both
"""

import os
import sys
import time
import json
import logging
import argparse
from typing import Tuple, List, Optional, Dict
from datetime import datetime

try:
    import yaml
except ImportError:
    yaml = None

try:
    import requests
    from urllib3.exceptions import InsecureRequestWarning
    requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)
except ImportError:
    print("[ERROR] requests library not installed. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)

# Import common utilities
try:
    from sim.common import (
        format_result, setup_simulator_logger,
        STATUS_OK, STATUS_REFUSED, STATUS_TIMEOUT, STATUS_AUTH_FAILED, STATUS_PROTO_ERROR,
        EXIT_OK, EXIT_REFUSED, EXIT_TIMEOUT, EXIT_AUTH_FAILED, EXIT_PROTO_ERROR,
        load_merged_config, validate_http_header_name, validate_http_header_value,
        load_payload_config, generate_payload, run_devices_parallel
    )
except ImportError:
    from common import (
        format_result, setup_simulator_logger,
        STATUS_OK, STATUS_REFUSED, STATUS_TIMEOUT, STATUS_AUTH_FAILED, STATUS_PROTO_ERROR,
        EXIT_OK, EXIT_REFUSED, EXIT_TIMEOUT, EXIT_AUTH_FAILED, EXIT_PROTO_ERROR,
        load_merged_config, validate_http_header_name, validate_http_header_value,
        load_payload_config, generate_payload, run_devices_parallel
    )


def get_default_config():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    raw = load_merged_config(base_dir)
    target = raw.get("global", {})
    http = raw.get("adapters", {}).get("http", {})
    tls = raw.get("tls", {})
    auth_cfg = http.get("auth", {})

    cfg = {
        "host": target.get("target_host", "127.0.0.1"),
        "port": http.get("port", 8080),
        "port_tls": http.get("port_tls", tls.get("ports", {}).get("https", 8443)),
        "path": http.get("path", "/api/v1/telemetry"),
        "path_template": http.get("path_template", "/api/v1/{token}/telemetry"),
        "access_token": http.get("access_token", "test_token_http"),
        "auth_mode": http.get("auth_mode", "header"),
        "auth_header_key": auth_cfg.get("header_key", ""),
        "auth_header_value": auth_cfg.get("header_value", ""),
        "auth_header_value_env": auth_cfg.get("header_value_env", ""),
        "auth_extra_headers": auth_cfg.get("extra_headers", {}),
        "config_headers": http.get("headers", {}),
        "downlink_path": http.get("downlink_path", "/api/v1/device/downlink"),
        "device_id": target.get("device_id", "sim_device_001"),
        "tls_enabled": tls.get("enabled", False),
        "insecure_skip_verify": tls.get("insecure_skip_verify", False),
    }
    return cfg


def setup_logger(args=None):
    return setup_simulator_logger("http", args=args)


def build_http_target(args, cfg):
    proto = "https" if args.tls else "http"
    auth_mode = args.auth_mode or cfg.get("auth_mode", "header")

    if auth_mode == "url_token" and args.token:
        if "{token}" in cfg.get("path_template", ""):
            resolved_path = cfg["path_template"].replace("{token}", args.token)
        else:
            resolved_path = f"{args.path.rstrip('/')}/{args.token}"
    else:
        resolved_path = args.path

    # Build Headers with strict priority and validation:
    # CLI flag > env var > config.local.yaml > config.yaml
    header_key = None
    if getattr(args, "header_key", None):
        header_key = args.header_key
    elif cfg.get("auth_header_key"):
        header_key = cfg["auth_header_key"]

    header_value = None
    if getattr(args, "header_value", None) is not None:
        header_value = args.header_value
    elif getattr(args, "header_value_env", None):
        header_value = os.environ.get(args.header_value_env, "")
    elif cfg.get("auth_header_value_env"):
        header_value = os.environ.get(cfg["auth_header_value_env"], "")
    elif cfg.get("auth_header_value"):
        header_value = cfg["auth_header_value"]

    headers = {}
    has_custom_auth = bool(header_key and header_value is not None)

    if not has_custom_auth:
        # Legacy fallback headers
        if cfg.get("config_headers"):
            headers.update(cfg["config_headers"])
        if args.token and auth_mode == "header":
            headers["X-Authorization"] = f"Bearer {args.token}"
            headers["Authorization"] = f"Bearer {args.token}"
    else:
        if not validate_http_header_name(header_key):
            raise ValueError(f"Invalid header key '{header_key}': contains invalid characters or control characters")
        if not validate_http_header_value(header_value):
            raise ValueError(f"Invalid header value for '{header_key}': contains CR, LF, or control characters")
        # Send verbatim without adding 'Bearer ' prefix
        headers[header_key] = header_value

    # Extra headers from config
    for ek, ev in cfg.get("auth_extra_headers", {}).items():
        if not validate_http_header_name(ek):
            raise ValueError(f"Invalid extra header key '{ek}'")
        if not validate_http_header_value(str(ev)):
            raise ValueError(f"Invalid extra header value for '{ek}'")
        headers[ek] = str(ev)

    # Extra headers from CLI --header "KEY=VALUE"
    if getattr(args, "extra_headers", None):
        for h_str in args.extra_headers:
            if "=" not in h_str:
                raise ValueError(f"Malformed --header argument '{h_str}'. Expected 'KEY=VALUE'.")
            k, v = h_str.split("=", 1)
            k = k.strip()
            v = v.strip()
            if not validate_http_header_name(k):
                raise ValueError(f"Invalid header name '{k}' in --header argument")
            if not validate_http_header_value(v):
                raise ValueError(f"Invalid header value for '{k}' in --header argument")
            headers[k] = v

    url = f"{proto}://{args.host}:{args.port}{resolved_path}"
    return url, headers


def do_connect(args, cfg, logger):
    url, headers = build_http_target(args, cfg)
    verify_cert = not args.insecure
    if args.tls and args.insecure:
        logger.warning("[!] WARNING: TLS certificate verification disabled via --insecure flag.")

    verify_display = "n/a (TLS off)" if not args.tls else ("False (--insecure)" if args.insecure else "True")
    logger.info(f"[*] [HTTP CONNECT CHECK] Testing connection to {url} (TLS: {args.tls}, VerifyCert: {verify_display})...")

    t0 = time.perf_counter()
    try:
        # Gửi GET request thăm dò không payload để kiểm tra phản hồi từ web server
        resp = requests.get(url, headers=headers, timeout=args.timeout, verify=verify_cert if args.tls else False)
        elapsed = time.perf_counter() - t0
        server_header = resp.headers.get("Server", "Unknown")
        status_line = f"HTTP {resp.status_code} {resp.reason} (Server: {server_header})"
        logger.info(f"[{datetime.now().isoformat()}] <--- Raw Response: {status_line}")

        if resp.status_code in (401, 403):
            logger.error(f"[!] Authentication/Authorization failed: {status_line}")
            return format_result("http", "connect", STATUS_AUTH_FAILED, status_line, stage="auth")
        else:
            # Bất kỳ mã HTTP nào (200, 204, 404, 405, 500...) đều chứng tỏ web server đang sống và phản hồi giao thức
            logger.info(f"[*] Valid HTTP protocol response received.")
            return format_result("http", "connect", STATUS_OK, status_line, stage="http_handshake")

    except requests.exceptions.SSLError as se:
        elapsed = time.perf_counter() - t0
        logger.error(f"[!] TLS/SSL Handshake failed: {se}")
        return format_result("http", "connect", STATUS_AUTH_FAILED, f"SSLError: {se}", stage="tls_handshake", elapsed=elapsed)
    except requests.exceptions.ConnectTimeout as cte:
        elapsed = time.perf_counter() - t0
        logger.error(f"[!] Connection timeout after {args.timeout}s: {cte}")
        return format_result("http", "connect", STATUS_TIMEOUT, str(cte), stage="tcp_connect", elapsed=elapsed)
    except requests.exceptions.ConnectionError as ce:
        elapsed = time.perf_counter() - t0
        ce_str = str(ce)
        if "10061" in ce_str or "refused" in ce_str.lower():
            logger.error(f"[!] Connection refused: {ce}")
            return format_result("http", "connect", STATUS_REFUSED, ce_str, stage="tcp_connect", elapsed=elapsed)
        elif "timeout" in ce_str.lower():
            logger.error(f"[!] Connection timeout: {ce}")
            return format_result("http", "connect", STATUS_TIMEOUT, ce_str, stage="tcp_connect", elapsed=elapsed)
        else:
            logger.error(f"[!] Network error: {ce}")
            return format_result("http", "connect", STATUS_REFUSED, ce_str, stage="tcp_connect", elapsed=elapsed)
    except requests.exceptions.Timeout as te:
        elapsed = time.perf_counter() - t0
        logger.error(f"[!] Request timeout after {args.timeout}s: {te}")
        return format_result("http", "connect", STATUS_TIMEOUT, str(te), stage="http_response", elapsed=elapsed)
    except Exception as e:
        logger.error(f"[!] Unexpected error during connect: {e}")
        return format_result("http", "connect", STATUS_PROTO_ERROR, str(e), stage="unknown")


def do_send(args, cfg, logger):
    url, headers = build_http_target(args, cfg)
    headers["Content-Type"] = "application/json"
    verify_cert = not args.insecure
    if args.tls and args.insecure:
        logger.warning("[!] WARNING: TLS certificate verification disabled via --insecure flag.")

    payload_cfg = load_payload_config("http", logger=logger)
    if payload_cfg:
        device_ids = payload_cfg.get("device_ids") or [args.device_id]
        attributes = payload_cfg.get("attributes", [])
        default_count = payload_cfg.get("count", 1)
        default_interval = payload_cfg.get("interval", 1.0)
    else:
        device_ids = [args.device_id]
        attributes = None
        default_count = 1
        default_interval = 1.0

    count = args.count if args.count is not None else default_count
    interval = args.interval if args.interval is not None else default_interval

    logger.info(f"[*] [HTTP SEND] Target: {url} | Devices: {len(device_ids)} | Count: {count} | Interval: {interval}s")

    def _http_worker(dev_id: str, dev_logger: logging.Logger) -> Tuple[str, str]:
        dev_success = 0
        dev_status = STATUS_OK
        last_resp_desc = None
        dev_t0 = time.time()
        i = 0
        while True:
            i += 1
            if count is not None and i > count:
                break
            if count is None and (time.time() - dev_t0 >= args.duration):
                break

            if attributes is not None:
                payload = generate_payload(attributes, device_id=dev_id, seq=i, logger=dev_logger)
            else:
                payload = {
                    "device_id": dev_id,
                    "seq": i,
                    "temperature": round(25.0 + i * 0.5, 2),
                    "humidity": round(60.0 - i * 0.2, 2),
                    "timestamp": int(time.time() * 1000)
                }
            body_str = json.dumps(payload)
            dev_logger.info(f"[{datetime.now().isoformat()}] ---> POST {url} | Payload: {body_str}")

            try:
                resp = requests.post(url, data=body_str, headers=headers, timeout=args.timeout, verify=verify_cert)
                server_hdr = resp.headers.get("Server", "Unknown")
                last_resp_desc = f"HTTP {resp.status_code} {resp.reason}"
                dev_logger.info(f"[{datetime.now().isoformat()}] <--- Raw Response Code: {resp.status_code} {resp.reason}")
                dev_logger.info(f"     Raw Response Body: {resp.text}")

                if 200 <= resp.status_code < 300:
                    dev_success += 1
                elif resp.status_code in (401, 403):
                    dev_logger.warning(f"[!] Platform rejected auth: {resp.status_code}")
                    return STATUS_AUTH_FAILED, f"AUTH_FAILED x1 ({last_resp_desc})"
                else:
                    dev_logger.warning(f"[!] Platform returned error: {resp.status_code}")
                    dev_status = STATUS_PROTO_ERROR
            except requests.exceptions.ConnectionError as ce:
                dev_logger.error(f"[!] Connection Refused to {url}: {ce}")
                return STATUS_REFUSED, f"REFUSED ({ce})"
            except requests.exceptions.Timeout as te:
                dev_logger.error(f"[!] Request Timeout after {args.timeout}s: {te}")
                return STATUS_TIMEOUT, f"TIMEOUT after {args.timeout}s"
            except Exception as e:
                dev_logger.error(f"[!] Unexpected error during send: {e}")
                return STATUS_PROTO_ERROR, f"PROTO_ERROR ({e})"

            if count is not None and i < count:
                time.sleep(interval)
            elif count is None:
                time.sleep(interval)

        if dev_success > 0 and dev_status == STATUS_OK:
            return STATUS_OK, f"OK x{dev_success}"
        elif dev_success > 0 and dev_status != STATUS_OK:
            return dev_status, f"{dev_status} (OK x{dev_success}, errors seen)"
        else:
            return dev_status, f"{dev_status} ({last_resp_desc or 'no 2xx'})"

    max_dev_timeout = (args.timeout * (count or 5) + (interval * (count or 5)) + 5.0) if count else (args.duration + 5.0)
    return run_devices_parallel("http", "send", device_ids, _http_worker, timeout_per_device=max_dev_timeout, args=args, logger=logger)


def do_listen(args, cfg, logger):
    proto = "https" if args.tls else "http"
    downlink_url = f"{proto}://{args.host}:{args.port}{args.downlink_path}"
    headers = {
        "Accept": "application/json"
    }
    _, target_headers = build_http_target(args, cfg)
    headers.update(target_headers)

    verify_cert = not args.insecure
    logger.info(f"[*] [HTTP LISTEN] Polling downlink at {downlink_url} for {args.duration}s...")
    logger.info("    (Note: If platform does not support HTTP downlink polling, this will report no downlink received)")

    start_time = time.time()
    received_any = False

    while time.time() - start_time < args.duration:
        try:
            resp = requests.get(downlink_url, headers=headers, timeout=2.0, verify=verify_cert)
            if resp.status_code == 200 and resp.text.strip():
                logger.info(f"[{datetime.now().isoformat()}] <=== [DOWNLINK RECEIVED] {resp.text}")
                received_any = True
            elif resp.status_code == 404:
                logger.info(f"[*] Downlink endpoint {downlink_url} returned 404 Not Found (Platform may not support HTTP downlink).")
                break
            elif resp.status_code in (401, 403):
                logger.error(f"[!] Auth failed on downlink endpoint: HTTP {resp.status_code}")
                return format_result("http", "listen", STATUS_AUTH_FAILED, f"HTTP {resp.status_code}", stage="auth")
        except requests.exceptions.RequestException:
            pass
        time.sleep(1.0)

    elapsed = round(time.time() - start_time, 1)
    if not received_any:
        logger.info(f"[*] Khong nhan duoc ban tin downlink nao sau {elapsed} giay (Platform chua co downlink HTTP).")
        return format_result("http", "listen", STATUS_OK, f"Recv:0 (no downlink received) after {elapsed}s", stage="downlink_idle")
    else:
        return format_result("http", "listen", STATUS_OK, "Recv:downlink(s) received", stage="downlink_received")


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    cfg = get_default_config()
    parser = argparse.ArgumentParser(
        description="HTTP IoT Protocol Simulator (Connect check, Send telemetry, Listen/poll downlinks)",
        epilog="Chế độ 'connect' chỉ bắt tay/đăng nhập tối thiểu. CẢNH BÁO WINDOWS: trên Windows loopback, cổng đóng có thể mất ~2s mới báo refused (WinError 10061); khuyến nghị dùng --timeout >= 3.0 cho connect.\nƯu tiên xác thực: cờ CLI (--header-key, --header-value, --header) > biến môi trường > config/config.local.yaml > config.yaml. Cặp header mới gửi giá trị nguyên văn (không tự thêm 'Bearer ')."
    )
    parser.add_argument("--mode", choices=["connect", "send", "listen", "both"], default="connect",
                        help="Operating mode: 'connect' (handshake only), 'send' telemetry, 'listen' for downlink, or 'both' (default: connect)")
    parser.add_argument("--host", default=cfg["host"], help=f"Target platform host (default: {cfg['host']})")
    parser.add_argument("--port", type=int, default=None, help="Target platform HTTP/HTTPS port (default: 8080 or 8443 if --tls)")
    parser.add_argument("--path", default=cfg["path"], help=f"Telemetry POST path (default: {cfg['path']})")
    parser.add_argument("--downlink-path", default=cfg["downlink_path"], help="Downlink polling path")
    parser.add_argument("--token", default=cfg["access_token"], help="Device access token")
    parser.add_argument("--auth-mode", choices=["header", "url_token"], default=cfg.get("auth_mode", "header"), help="Authentication mode: header or url_token")
    parser.add_argument("--header-key", default=None, help="Custom HTTP header key for authentication (e.g. api_key, Authorization)")
    parser.add_argument("--header-value", default=None, help="Custom HTTP header value (sent verbatim, no Bearer prefix auto-added)")
    parser.add_argument("--header-value-env", default=None, help="Environment variable name containing custom HTTP header value")
    parser.add_argument("--header", dest="extra_headers", action="append", default=[], help="Additional HTTP header in KEY=VALUE format (repeatable)")
    parser.add_argument("--device-id", default=cfg["device_id"], help=f"Simulator device ID (default: {cfg['device_id']})")
    parser.add_argument("--tls", action="store_true", default=cfg.get("tls_enabled", False), help="Enable TLS/HTTPS")
    parser.add_argument("--insecure", action="store_true", default=cfg.get("insecure_skip_verify", False), help="Skip TLS certificate verification (default: verify certificates)")
    parser.add_argument("--count", type=int, default=None, help="Number of telemetry messages to send (default: from payload config or 1)")
    parser.add_argument("--interval", type=float, default=None, help="Interval between sends in seconds (default: from payload config or 1.0)")
    parser.add_argument("--duration", type=float, default=5.0, help="Listen duration in seconds (default: 5.0)")
    parser.add_argument("--timeout", type=float, default=3.0, help="HTTP request timeout in seconds (default: 3.0)")

    args = parser.parse_args()
    logger = setup_logger(args)

    if args.port is None:
        args.port = cfg["port_tls"] if args.tls else cfg["port"]

    try:
        build_http_target(args, cfg)
    except ValueError as ve:
        logger.error(f"[!] Header validation failed: {ve}")
        rc = format_result("http", args.mode, STATUS_PROTO_ERROR, f"[stage:header_validation] Header validation error: {ve}", stage="header_validation")
        sys.exit(rc)

    if args.mode == "connect":
        rc = do_connect(args, cfg, logger)
    elif args.mode == "send":
        rc = do_send(args, cfg, logger)
    elif args.mode == "listen":
        rc = do_listen(args, cfg, logger)
    else:  # both
        rc_send = do_send(args, cfg, logger)
        rc_listen = do_listen(args, cfg, logger)
        rc = rc_send if rc_send != 0 else rc_listen

    sys.exit(rc)


if __name__ == "__main__":
    sys.exit(main())
