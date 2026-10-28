#!/usr/bin/env python3
"""
WebSocket IoT Protocol Simulator CLI
Protocol #9: WebSocket Client (websockets async)
Usage:
  python sim/websocket_sim.py --mode connect
  python sim/websocket_sim.py --mode connect --tls --insecure
  python sim/websocket_sim.py --mode both --count 3 --duration 8
  python sim/websocket_sim.py --mode send --count 5
  python sim/websocket_sim.py --mode listen --duration 10
"""

import os
import sys
import ssl
import time
import json
import logging
import asyncio
import argparse
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
    format_result, classify_socket_error, tcp_preflight_probe
)

try:
    import yaml
except ImportError:
    yaml = None

try:
    import websockets
    try:
        from websockets.exceptions import InvalidStatus
    except ImportError:
        InvalidStatus = None
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=DeprecationWarning)
            from websockets.exceptions import InvalidStatusCode
    except ImportError:
        InvalidStatusCode = None
    from websockets.exceptions import ConnectionClosedError
except ImportError:
    print("[ERROR] websockets not installed. Install with: pip install websockets", file=sys.stderr)
    sys.exit(1)


def get_default_config():
    cfg_path = os.path.join(base_dir, "config", "config.yaml")
    cfg = {
        "host": "127.0.0.1",
        "port": 8080,
        "port_tls": 8443,
        "path": "/api/v1/ws",
        "access_token": "test_token_ws",
        "tls_enabled": False,
        "insecure_skip_verify": False
    }
    if yaml and os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
                target = raw.get("target", {})
                ws = raw.get("adapters", {}).get("websocket", {})
                tls = raw.get("tls", {})
                cfg["host"] = target.get("host", cfg["host"])
                cfg["port"] = ws.get("port", cfg["port"])
                cfg["path"] = ws.get("path", cfg["path"])
                cfg["access_token"] = ws.get("access_token", cfg["access_token"])
                cfg["tls_enabled"] = tls.get("enabled", cfg["tls_enabled"])
                cfg["insecure_skip_verify"] = tls.get("insecure_skip_verify", cfg["insecure_skip_verify"])
                ports = tls.get("ports", {})
                cfg["port_tls"] = ports.get("https", cfg["port_tls"])
        except Exception as e:
            sys.stderr.write(f"[WARN] Failed to read config.yaml: {e}\n")
    return cfg


def setup_logger():
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "websocket.log")

    logger = logging.getLogger("websocket_sim")
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


async def run_ws_sim(args, cfg, logger):
    scheme = "wss" if args.tls else "ws"
    uri = f"{scheme}://{args.host}:{args.port}{args.path}"
    if args.token:
        sep = "&" if "?" in uri else "?"
        uri += f"{sep}token={args.token}"

    logger.info(f"[*] Starting WebSocket Simulator in '{args.mode}' mode (TLS: {args.tls})...")
    logger.info(f"    Target URI: {uri}")

    ssl_context = None
    if args.tls:
        ssl_context = ssl.create_default_context()
        if args.insecure:
            logger.warning("[!] WARNING: TLS certificate verification is disabled (--insecure)")
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        else:
            ssl_context.verify_mode = ssl.CERT_REQUIRED

    # Pre-flight TCP Connect Check
    is_connected, probe_status, probe_detail, probe_elapsed = tcp_preflight_probe(
        args.host, args.port, timeout=args.timeout
    )
    if not is_connected:
        logger.error(f"[!] TCP connect failed to {args.host}:{args.port}: {probe_detail}")
        return format_result("websocket", args.mode, probe_status, probe_detail, elapsed=probe_elapsed)

    handshake_exceptions = tuple(exc for exc in (InvalidStatus, InvalidStatusCode) if exc is not None)

    try:
        ws = await asyncio.wait_for(websockets.connect(uri, ssl=ssl_context), timeout=args.timeout)
        logger.info(f"[{datetime.now().isoformat()}] <--- [WS CONNECTED] WebSocket Handshake (101 Switching Protocols) successful!")
    except asyncio.TimeoutError:
        logger.error(f"[!] WebSocket handshake timeout after {args.timeout}s at {uri}")
        return format_result("websocket", args.mode, STATUS_TIMEOUT, f"Timeout after {args.timeout}s waiting for handshake", stage="handshake")
    except handshake_exceptions as isc:
        code = None
        server_hdr = ""
        if hasattr(isc, "response") and isc.response is not None:
            code = getattr(isc.response, "status_code", None)
            headers = getattr(isc.response, "headers", {})
            if hasattr(headers, "get_all"):
                all_s = headers.get_all("Server")
                server_hdr = all_s[0] if all_s else ""
            elif hasattr(headers, "get"):
                try:
                    server_hdr = headers.get("server", headers.get("Server", ""))
                except Exception:
                    pass
        if code is None:
            code = getattr(isc, "status_code", None)
        server_str = f" (Server: {server_hdr})" if server_hdr else ""
        logger.error(f"[!] WebSocket handshake rejected with HTTP {code}{server_str}")
        if code in (401, 403):
            return format_result("websocket", args.mode, STATUS_AUTH_FAILED,
                                 f"HTTP {code} Unauthorized/Forbidden{server_str}", stage="auth")
        elif code == 404:
            return format_result("websocket", args.mode, STATUS_PROTO_ERROR,
                                 f"HTTP 404 Not Found (server answered but path '{args.path}' does not exist){server_str}",
                                 stage="handshake")
        else:
            return format_result("websocket", args.mode, STATUS_PROTO_ERROR,
                                 f"HTTP {code} Handshake Rejected{server_str}", stage="handshake")
    except ssl.SSLError as se:
        logger.error(f"[!] TLS/SSL handshake error: {se}")
        return format_result("websocket", args.mode, STATUS_AUTH_FAILED, f"SSLError: {se}", stage="tls_handshake")
    except (ConnectionRefusedError, OSError) as oe:
        status, detail, code = classify_socket_error(oe, stage="tcp_connect")
        logger.error(f"[!] Socket connection error to {uri}: {oe}")
        return format_result("websocket", args.mode, status, detail)
    except Exception as e:
        code = None
        server_hdr = ""
        if hasattr(e, "response") and getattr(e, "response", None) is not None:
            code = getattr(e.response, "status_code", None)
            headers = getattr(e.response, "headers", {})
            if hasattr(headers, "get_all"):
                all_s = headers.get_all("Server")
                server_hdr = all_s[0] if all_s else ""
            elif hasattr(headers, "get"):
                try:
                    server_hdr = headers.get("server", headers.get("Server", ""))
                except Exception:
                    pass
        elif hasattr(e, "status_code"):
            code = getattr(e, "status_code", None)
        server_str = f" (Server: {server_hdr})" if server_hdr else ""
        if code in (401, 403):
            return format_result("websocket", args.mode, STATUS_AUTH_FAILED,
                                 f"HTTP {code} Unauthorized/Forbidden{server_str}", stage="auth")
        elif code == 404:
            return format_result("websocket", args.mode, STATUS_PROTO_ERROR,
                                 f"HTTP 404 Not Found (server answered but path '{args.path}' does not exist){server_str}",
                                 stage="handshake")
        elif code is not None:
            return format_result("websocket", args.mode, STATUS_PROTO_ERROR,
                                 f"HTTP {code} Handshake Rejected{server_str}", stage="handshake")
        status, detail, code_val = classify_socket_error(e, stage="handshake")
        return format_result("websocket", args.mode, status, detail)

    # Mode: CONNECT only
    if args.mode == "connect":
        await ws.close()
        logger.info("[*] WebSocket 101 handshake successful. Exiting connect mode.")
        return format_result("websocket", "connect", STATUS_OK, "WebSocket 101 Switching Protocols upgraded successfully", stage="handshake")

    messages_received = []

    async def listen_loop():
        try:
            async for msg in ws:
                logger.info(f"[{datetime.now().isoformat()}] <=== [WS DOWNLINK] {msg}")
                messages_received.append(msg)
                try:
                    data = json.loads(msg)
                    if "cmd" in data or "ping" in data:
                        reply = json.dumps({"status": "ACK", "ts": int(time.time()*1000)})
                        await ws.send(reply)
                        logger.info(f"[{datetime.now().isoformat()}] ===> [WS AUTO REPLY] {reply}")
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass
        except Exception as le:
            logger.warning(f"[!] WS listen error: {le}")

    listen_task = asyncio.create_task(listen_loop())

    # SEND or BOTH
    if args.mode in ["send", "both"]:
        logger.info(f"[*] Sending {args.count} WebSocket telemetry frame(s)...")
        for i in range(1, args.count + 1):
            telemetry = {
                "device_id": args.device_id,
                "seq": i,
                "temperature": round(25.5 + i * 0.2, 2),
                "humidity": round(52.0 - i * 0.4, 2),
                "timestamp": int(time.time() * 1000)
            }
            body = json.dumps(telemetry)
            try:
                await ws.send(body)
                logger.info(f"[{datetime.now().isoformat()}] ---> WS SEND: {body}")
            except Exception as se:
                logger.error(f"[!] WS send failed: {se}")
                break
            if i < args.count:
                await asyncio.sleep(args.interval)

    # LISTEN or BOTH
    if args.mode in ["listen", "both"]:
        logger.info(f"[*] Listening for WebSocket downlinks for {args.duration}s...")
        await asyncio.sleep(args.duration)
        if len(messages_received) == 0:
            logger.info(f"[*] Khong nhan duoc ban tin WebSocket downlink nao sau {args.duration} giay.")

    listen_task.cancel()
    await ws.close()
    logger.info("[*] WebSocket Simulator finished cleanly.")
    if args.mode == "listen":
        if len(messages_received) == 0:
            detail = f"Recv:0 (no downlink received) after {args.duration}s"
        else:
            detail = f"Recv:{len(messages_received)} downlink(s) received"
    elif args.mode == "both":
        if len(messages_received) == 0:
            detail = f"Sent:{args.count}, Recv:0 (no downlink received)"
        else:
            detail = f"Sent:{args.count}, Recv:{len(messages_received)}"
    else:
        detail = "Completed cleanly"
    return format_result("websocket", args.mode, STATUS_OK, detail, stage="channel")


def main():
    cfg = get_default_config()
    parser = argparse.ArgumentParser(
        description="WebSocket IoT Protocol Simulator CLI (websockets async)",
        epilog="Chế độ 'connect' bắt tay 101 Switching Protocols. CẢNH BÁO WINDOWS: trên Windows loopback, cổng đóng có thể mất ~2s mới báo refused (WinError 10061); khuyến nghị dùng --timeout >= 3.0 cho connect."
    )
    parser.add_argument("--mode", choices=["connect", "send", "listen", "both"], default="connect",
                        help="Operating mode: 'connect', 'send', 'listen', or 'both' (default: connect)")
    parser.add_argument("--host", default=cfg["host"], help=f"WebSocket host (default: {cfg['host']})")
    parser.add_argument("--port", type=int, default=None, help="WebSocket port (default: 8080 or 8443 if --tls)")
    parser.add_argument("--path", default=cfg["path"], help=f"WebSocket endpoint path (default: {cfg['path']})")
    parser.add_argument("--token", default=cfg["access_token"], help="Access token")
    parser.add_argument("--device-id", default="sim_ws_001", help="Device ID")
    parser.add_argument("--tls", action="store_true", default=cfg.get("tls_enabled", False), help="Enable WSS (TLS)")
    parser.add_argument("--insecure", action="store_true", default=cfg.get("insecure_skip_verify", False),
                        help="Skip TLS certificate verification (insecure)")
    parser.add_argument("--count", type=int, default=1, help="Frame count to send (default: 1)")
    parser.add_argument("--interval", type=float, default=1.0, help="Interval between sends in seconds (default: 1.0)")
    parser.add_argument("--duration", type=float, default=5.0, help="Listen duration in seconds (default: 5.0)")
    parser.add_argument("--timeout", type=float, default=3.0, help="Handshake timeout in seconds (default: 3.0)")

    args = parser.parse_args()
    logger = setup_logger()

    if args.port is None:
        args.port = cfg["port_tls"] if args.tls else cfg["port"]

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        rc = loop.run_until_complete(run_ws_sim(args, cfg, logger))
    finally:
        loop.close()

    sys.exit(rc)


if __name__ == "__main__":
    main()
