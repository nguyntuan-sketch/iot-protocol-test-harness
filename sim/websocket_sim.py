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
    format_result, classify_socket_error, tcp_preflight_probe, setup_simulator_logger,
    load_merged_config, validate_http_header_name, validate_http_header_value,
    load_payload_config, generate_payload, run_devices_parallel
)
from typing import Tuple, List, Optional, Dict

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
    raw = load_merged_config(base_dir)
    target = raw.get("global", {})
    ws = raw.get("adapters", {}).get("websocket", {})
    tls = raw.get("tls", {})
    auth_cfg = ws.get("auth", {})

    cfg = {
        "host": target.get("target_host", "127.0.0.1"),
        "port": ws.get("port", 8080),
        "port_tls": tls.get("ports", {}).get("https", 8443),
        "path": ws.get("path", "/ws/telemetry"),
        "access_token": ws.get("access_token", "test_token_ws"),
        "device_id": target.get("device_id", "sim_device_001"),
        "auth_header_key": auth_cfg.get("header_key", ""),
        "auth_header_value": auth_cfg.get("header_value", ""),
        "auth_header_value_env": auth_cfg.get("header_value_env", ""),
        "auth_extra_headers": auth_cfg.get("extra_headers", {}),
        "tls_enabled": tls.get("enabled", False),
        "insecure_skip_verify": tls.get("insecure_skip_verify", False)
    }
    return cfg


def setup_logger(args=None):
    return setup_simulator_logger("websocket", args=args)


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

    # Build Handshake Headers
    headers = {}
    header_key = getattr(args, "header_key", None) or cfg.get("auth_header_key")
    header_value = None
    if getattr(args, "header_value", None) is not None:
        header_value = args.header_value
    elif getattr(args, "header_value_env", None):
        header_value = os.environ.get(args.header_value_env, "")
    elif cfg.get("auth_header_value_env"):
        header_value = os.environ.get(cfg["auth_header_value_env"], "")
    elif cfg.get("auth_header_value"):
        header_value = cfg["auth_header_value"]

    if header_key and header_value is not None:
        if not validate_http_header_name(header_key):
            return format_result("websocket", args.mode, STATUS_PROTO_ERROR, f"[stage:header_validation] Invalid header key '{header_key}'", stage="header_validation")
        if not validate_http_header_value(header_value):
            return format_result("websocket", args.mode, STATUS_PROTO_ERROR, f"[stage:header_validation] Invalid header value for '{header_key}'", stage="header_validation")
        headers[header_key] = header_value

    for ek, ev in cfg.get("auth_extra_headers", {}).items():
        if not validate_http_header_name(ek) or not validate_http_header_value(str(ev)):
            return format_result("websocket", args.mode, STATUS_PROTO_ERROR, f"[stage:header_validation] Invalid extra header '{ek}'", stage="header_validation")
        headers[ek] = str(ev)

    if getattr(args, "extra_headers", None):
        for h_str in args.extra_headers:
            if "=" not in h_str:
                return format_result("websocket", args.mode, STATUS_PROTO_ERROR, f"[stage:header_validation] Malformed --header '{h_str}'", stage="header_validation")
            k, v = h_str.split("=", 1)
            k, v = k.strip(), v.strip()
            if not validate_http_header_name(k) or not validate_http_header_value(v):
                return format_result("websocket", args.mode, STATUS_PROTO_ERROR, f"[stage:header_validation] Invalid header '{k}'", stage="header_validation")
            headers[k] = v

    connect_kwargs = {}
    if headers:
        import inspect
        sig = inspect.signature(websockets.connect)
        if "additional_headers" in sig.parameters:
            connect_kwargs["additional_headers"] = headers
        elif "extra_headers" in sig.parameters:
            connect_kwargs["extra_headers"] = headers

    try:
        ws = await asyncio.wait_for(websockets.connect(uri, ssl=ssl_context, **connect_kwargs), timeout=args.timeout)
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


def do_send_parallel(args, cfg, logger):
    scheme = "wss" if args.tls else "ws"
    uri = f"{scheme}://{args.host}:{args.port}{args.path}"
    if args.token:
        sep = "&" if "?" in uri else "?"
        uri += f"{sep}token={args.token}"

    payload_cfg = load_payload_config("websocket", logger=logger)
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

    logger.info(f"[*] [WS SEND] Target: {uri} | Devices: {len(device_ids)} | Count: {count} | Interval: {interval}s")

    # Build Handshake Headers
    headers = {}
    header_key = getattr(args, "header_key", None) or cfg.get("auth_header_key")
    header_value = None
    if getattr(args, "header_value", None) is not None:
        header_value = args.header_value
    elif getattr(args, "header_value_env", None):
        header_value = os.environ.get(args.header_value_env, "")
    elif cfg.get("auth_header_value_env"):
        header_value = os.environ.get(cfg["auth_header_value_env"], "")
    elif cfg.get("auth_header_value"):
        header_value = cfg["auth_header_value"]

    if header_key and header_value is not None:
        if not validate_http_header_name(header_key):
            return format_result("websocket", args.mode, STATUS_PROTO_ERROR, f"[stage:header_validation] Invalid header key '{header_key}'", stage="header_validation")
        if not validate_http_header_value(header_value):
            return format_result("websocket", args.mode, STATUS_PROTO_ERROR, f"[stage:header_validation] Invalid header value for '{header_key}'", stage="header_validation")
        headers[header_key] = header_value

    for ek, ev in cfg.get("auth_extra_headers", {}).items():
        if not validate_http_header_name(ek) or not validate_http_header_value(str(ev)):
            return format_result("websocket", args.mode, STATUS_PROTO_ERROR, f"[stage:header_validation] Invalid extra header '{ek}'", stage="header_validation")
        headers[ek] = str(ev)

    if getattr(args, "extra_headers", None):
        for h_str in args.extra_headers:
            if "=" not in h_str:
                return format_result("websocket", args.mode, STATUS_PROTO_ERROR, f"[stage:header_validation] Malformed --header '{h_str}'", stage="header_validation")
            k, v = h_str.split("=", 1)
            k, v = k.strip(), v.strip()
            if not validate_http_header_name(k) or not validate_http_header_value(v):
                return format_result("websocket", args.mode, STATUS_PROTO_ERROR, f"[stage:header_validation] Invalid header '{k}'", stage="header_validation")
            headers[k] = v

    connect_kwargs = {}
    if headers:
        import inspect
        sig = inspect.signature(websockets.connect)
        if "additional_headers" in sig.parameters:
            connect_kwargs["additional_headers"] = headers
        elif "extra_headers" in sig.parameters:
            connect_kwargs["extra_headers"] = headers

    handshake_exceptions = tuple(exc for exc in (InvalidStatus, InvalidStatusCode) if exc is not None)

    def _ws_worker(dev_id: str, dev_logger: logging.Logger) -> Tuple[str, str]:
        sub_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(sub_loop)

        async def _async_run():
            ssl_context = None
            if args.tls:
                ssl_context = ssl.create_default_context()
                if args.insecure:
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE
                else:
                    ssl_context.verify_mode = ssl.CERT_REQUIRED

            try:
                ws = await asyncio.wait_for(websockets.connect(uri, ssl=ssl_context, **connect_kwargs), timeout=args.timeout)
            except asyncio.TimeoutError:
                dev_logger.error(f"[!] WebSocket handshake timeout after {args.timeout}s at {uri}")
                return STATUS_TIMEOUT, f"TIMEOUT after {args.timeout}s"
            except handshake_exceptions as isc:
                code = getattr(getattr(isc, "response", None), "status_code", getattr(isc, "status_code", None))
                dev_logger.error(f"[!] WebSocket handshake rejected with HTTP {code}")
                if code in (401, 403):
                    return STATUS_AUTH_FAILED, f"AUTH_FAILED (HTTP {code})"
                elif code == 404:
                    return STATUS_PROTO_ERROR, f"PROTO_ERROR (HTTP 404)"
                else:
                    return STATUS_PROTO_ERROR, f"PROTO_ERROR (HTTP {code})"
            except ssl.SSLError as se:
                dev_logger.error(f"[!] TLS/SSL handshake error: {se}")
                return STATUS_AUTH_FAILED, f"AUTH_FAILED (SSLError: {se})"
            except (ConnectionRefusedError, OSError) as oe:
                st, det, _ = classify_socket_error(oe, stage="tcp_connect")
                dev_logger.error(f"[!] Socket connection error to {uri}: {oe}")
                return st, f"{st} ({oe})"
            except Exception as e:
                dev_logger.error(f"[!] Handshake error: {e}")
                return STATUS_PROTO_ERROR, f"PROTO_ERROR ({type(e).__name__})"

            sent_count = 0
            try:
                for i in range(1, count + 1):
                    if attributes is not None:
                        telemetry = generate_payload(attributes, device_id=dev_id, seq=i, logger=dev_logger)
                    else:
                        telemetry = {
                            "device_id": dev_id,
                            "seq": i,
                            "temperature": round(25.5 + i * 0.2, 2),
                            "humidity": round(52.0 - i * 0.4, 2),
                            "timestamp": int(time.time() * 1000)
                        }
                    body = json.dumps(telemetry)
                    await ws.send(body)
                    dev_logger.info(f"[{datetime.now().isoformat()}] ---> WS SEND: {body}")
                    sent_count += 1
                    if i < count:
                        await asyncio.sleep(interval)
            except Exception as se:
                dev_logger.error(f"[!] WS send failed: {se}")
                try:
                    await ws.close()
                except Exception:
                    pass
                return STATUS_PROTO_ERROR, f"PROTO_ERROR ({type(se).__name__}: {se})"

            try:
                await ws.close()
            except Exception:
                pass
            return STATUS_OK, f"OK x{sent_count}"

        try:
            return sub_loop.run_until_complete(_async_run())
        finally:
            sub_loop.close()

    max_dev_timeout = (args.timeout * (count or 5) + (interval * (count or 5)) + 5.0)
    return run_devices_parallel("websocket", args.mode, device_ids, _ws_worker, timeout_per_device=max_dev_timeout, args=args, logger=logger)


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
    parser.add_argument("--header-key", default=None, help="Custom header key for WebSocket handshake (e.g. api_key, Authorization)")
    parser.add_argument("--header-value", default=None, help="Custom header value for WebSocket handshake")
    parser.add_argument("--header-value-env", default=None, help="Environment variable name for custom header value")
    parser.add_argument("--header", dest="extra_headers", action="append", default=[], help="Additional handshake header in KEY=VALUE format (repeatable)")
    parser.add_argument("--device-id", default=cfg.get("device_id", "sim_device_001"), help=f"Device ID (default: {cfg.get('device_id', 'sim_device_001')})")
    parser.add_argument("--tls", action="store_true", default=cfg.get("tls_enabled", False), help="Enable WSS (TLS)")
    parser.add_argument("--insecure", action="store_true", default=cfg.get("insecure_skip_verify", False),
                        help="Skip TLS certificate verification (insecure)")
    parser.add_argument("--count", type=int, default=None, help="Frame count to send (default: from payload config or 1)")
    parser.add_argument("--interval", type=float, default=None, help="Interval between sends in seconds (default: from payload config or 1.0)")
    parser.add_argument("--duration", type=float, default=5.0, help="Listen duration in seconds (default: 5.0)")
    parser.add_argument("--timeout", type=float, default=3.0, help="Handshake timeout in seconds (default: 3.0)")

    args = parser.parse_args()
    logger = setup_logger(args)

    if args.port is None:
        args.port = cfg["port_tls"] if args.tls else cfg["port"]

    if args.mode in ["send", "both"]:
        rc = do_send_parallel(args, cfg, logger)
    else:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            rc = loop.run_until_complete(run_ws_sim(args, cfg, logger))
        finally:
            loop.close()

    sys.exit(rc)


if __name__ == "__main__":
    main()
