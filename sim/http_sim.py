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
        EXIT_OK, EXIT_REFUSED, EXIT_TIMEOUT, EXIT_AUTH_FAILED, EXIT_PROTO_ERROR
    )
except ImportError:
    # Fallback if run standalone from root
    from common import (
        format_result, setup_simulator_logger,
        STATUS_OK, STATUS_REFUSED, STATUS_TIMEOUT, STATUS_AUTH_FAILED, STATUS_PROTO_ERROR,
        EXIT_OK, EXIT_REFUSED, EXIT_TIMEOUT, EXIT_AUTH_FAILED, EXIT_PROTO_ERROR
    )


def get_default_config():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg_path = os.path.join(base_dir, "config", "config.yaml")
    cfg = {
        "host": "127.0.0.1",
        "port": 8080,
        "port_tls": 8443,
        "path": "/api/v1/telemetry",
        "path_template": "/api/v1/{token}/telemetry",
        "access_token": "test_token_http",
        "auth_mode": "header",
        "downlink_path": "/api/v1/device/downlink",
        "tls_enabled": False,
        "insecure_skip_verify": False
    }
    if yaml and os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
                target = raw.get("target", {})
                http = raw.get("adapters", {}).get("http", {})
                tls = raw.get("tls", {})
                cfg["host"] = target.get("host", cfg["host"])
                cfg["port"] = http.get("port", cfg["port"])
                cfg["path"] = http.get("path", cfg["path"])
                cfg["path_template"] = http.get("path_template", cfg["path_template"])
                cfg["access_token"] = http.get("access_token", cfg["access_token"])
                cfg["auth_mode"] = http.get("auth_mode", cfg["auth_mode"])
                cfg["tls_enabled"] = tls.get("enabled", cfg["tls_enabled"])
                cfg["insecure_skip_verify"] = tls.get("insecure_skip_verify", cfg["insecure_skip_verify"])
                ports = tls.get("ports", {})
                cfg["port_tls"] = ports.get("https", cfg["port_tls"])
        except Exception as e:
            sys.stderr.write(f"[WARN] Failed to read config.yaml: {e}\n")
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
        headers = {}
    else:
        resolved_path = args.path
        headers = {}
        if args.token:
            headers["X-Authorization"] = f"Bearer {args.token}"
            headers["Authorization"] = f"Bearer {args.token}"

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

    logger.info(f"[*] [HTTP SEND] Target: {url} | AuthMode: {args.auth_mode or cfg.get('auth_mode')} | Count: {args.count}")
    success_count = 0
    last_status = None

    for i in range(1, args.count + 1):
        payload = {
            "device_id": args.device_id,
            "seq": i,
            "temperature": round(25.0 + i * 0.5, 2),
            "humidity": round(60.0 - i * 0.2, 2),
            "timestamp": int(time.time() * 1000)
        }
        body_str = json.dumps(payload)
        logger.info(f"[{datetime.now().isoformat()}] ---> POST {url} | Payload: {body_str}")

        try:
            resp = requests.post(url, data=body_str, headers=headers, timeout=args.timeout, verify=verify_cert)
            server_hdr = resp.headers.get("Server", "Unknown")
            last_status = f"HTTP {resp.status_code} {resp.reason} (Server: {server_hdr})"
            logger.info(f"[{datetime.now().isoformat()}] <--- Raw Response Code: {resp.status_code} {resp.reason}")
            logger.info(f"     Raw Response Body: {resp.text}")

            if 200 <= resp.status_code < 300:
                success_count += 1
            elif resp.status_code in (401, 403):
                logger.warning(f"[!] Platform rejected auth: {resp.status_code}")
                return format_result("http", "send", STATUS_AUTH_FAILED, last_status, stage="auth")
            else:
                logger.warning(f"[!] Platform returned non-2xx status: {resp.status_code}")
        except requests.exceptions.ConnectionError as ce:
            logger.error(f"[!] Connection Refused / Cannot connect to {url}: {ce}")
            return format_result("http", "send", STATUS_REFUSED, str(ce), stage="tcp_connect")
        except requests.exceptions.Timeout as te:
            logger.error(f"[!] Request Timeout after {args.timeout}s: {te}")
            return format_result("http", "send", STATUS_TIMEOUT, str(te), stage="http_response")
        except Exception as e:
            logger.error(f"[!] Unexpected error during send: {e}")
            return format_result("http", "send", STATUS_PROTO_ERROR, str(e), stage="send")

        if i < args.count:
            time.sleep(args.interval)

    logger.info(f"[*] Completed send: {success_count}/{args.count} requests returned 2xx status.")
    if success_count > 0:
        return format_result("http", "send", STATUS_OK, last_status, stage="telemetry_accepted")
    else:
        return format_result("http", "send", STATUS_PROTO_ERROR, last_status or "No 2xx responses", stage="http_status")


def do_listen(args, cfg, logger):
    proto = "https" if args.tls else "http"
    downlink_url = f"{proto}://{args.host}:{args.port}{args.downlink_path}"
    headers = {
        "Accept": "application/json"
    }
    if args.token:
        headers["X-Authorization"] = f"Bearer {args.token}"
        headers["Authorization"] = f"Bearer {args.token}"

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
        epilog="Chế độ 'connect' chỉ bắt tay/đăng nhập tối thiểu. CẢNH BÁO WINDOWS: trên Windows loopback, cổng đóng có thể mất ~2s mới báo refused (WinError 10061); khuyến nghị dùng --timeout >= 3.0 cho connect."
    )
    parser.add_argument("--mode", choices=["connect", "send", "listen", "both"], default="connect",
                        help="Operating mode: 'connect' (handshake only), 'send' telemetry, 'listen' for downlink, or 'both' (default: connect)")
    parser.add_argument("--host", default=cfg["host"], help=f"Target platform host (default: {cfg['host']})")
    parser.add_argument("--port", type=int, default=None, help="Target platform HTTP/HTTPS port (default: 8080 or 8443 if --tls)")
    parser.add_argument("--path", default=cfg["path"], help=f"Telemetry POST path (default: {cfg['path']})")
    parser.add_argument("--downlink-path", default=cfg["downlink_path"], help="Downlink polling path")
    parser.add_argument("--token", default=cfg["access_token"], help="Device access token")
    parser.add_argument("--auth-mode", choices=["header", "url_token"], default=cfg.get("auth_mode", "header"), help="Authentication mode: header or url_token")
    parser.add_argument("--device-id", default="sim_http_001", help="Simulator device ID")
    parser.add_argument("--tls", action="store_true", default=cfg.get("tls_enabled", False), help="Enable TLS/HTTPS")
    parser.add_argument("--insecure", action="store_true", default=cfg.get("insecure_skip_verify", False), help="Skip TLS certificate verification (default: verify certificates)")
    parser.add_argument("--count", type=int, default=1, help="Number of telemetry messages to send (default: 1)")
    parser.add_argument("--interval", type=float, default=1.0, help="Interval between sends in seconds (default: 1.0)")
    parser.add_argument("--duration", type=float, default=5.0, help="Listen duration in seconds (default: 5.0)")
    parser.add_argument("--timeout", type=float, default=3.0, help="HTTP request timeout in seconds (default: 3.0)")

    args = parser.parse_args()
    logger = setup_logger(args)

    if args.port is None:
        args.port = cfg["port_tls"] if args.tls else cfg["port"]

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
    main()
