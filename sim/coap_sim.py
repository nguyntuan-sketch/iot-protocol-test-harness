#!/usr/bin/env python3
"""
CoAP IoT Protocol Simulator CLI
Protocol #3: CoAP Client (aiocoap async)
Usage:
  python sim/coap_sim.py --mode connect
  python sim/coap_sim.py --mode connect --tls
  python sim/coap_sim.py --mode send --count 3 --interval 1.0
  python sim/coap_sim.py --mode listen --duration 5.0
  python sim/coap_sim.py --mode both
"""

import os
import sys
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
    format_result, classify_socket_error, setup_simulator_logger
)

try:
    import yaml
except ImportError:
    yaml = None

try:
    import aiocoap
    import aiocoap.error as coap_error
    from aiocoap import Message, Code, Context
except ImportError:
    print("[ERROR] aiocoap not installed. Install with: pip install aiocoap", file=sys.stderr)
    sys.exit(1)


def get_default_config():
    cfg_path = os.path.join(base_dir, "config", "config.yaml")
    cfg = {
        "host": "127.0.0.1",
        "port": 5683,
        "port_tls": 5684,
        "path": "api/v1/telemetry",
        "access_token": "test_token_coap",
        "message_type": "CON",
        "downlink_path": "api/v1/device/downlink",
        "tls_enabled": False
    }
    if yaml and os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
                target = raw.get("target", {})
                cp = raw.get("adapters", {}).get("coap", {})
                tls = raw.get("tls", {})
                cfg["host"] = target.get("host", cfg["host"])
                cfg["port"] = cp.get("port", cfg["port"])
                cfg["path"] = cp.get("path", cfg["path"])
                cfg["access_token"] = cp.get("access_token", cfg["access_token"])
                cfg["message_type"] = cp.get("message_type", cfg["message_type"])
                cfg["tls_enabled"] = tls.get("enabled", cfg["tls_enabled"])
                ports = tls.get("ports", {})
                cfg["port_tls"] = ports.get("coap_dtls", cfg["port_tls"])
        except Exception as e:
            sys.stderr.write(f"[WARN] Failed to read config.yaml: {e}\n")
    return cfg


def setup_logger(args=None):
    return setup_simulator_logger("coap", args=args)


def classify_coap_network_error(err_str: str) -> tuple[str, str]:
    """Distinguish ICMP port unreachable / connection reset from silent timeout."""
    err_lower = err_str.lower()
    if any(k in err_lower for k in ["networkerror", "network error", "1234", "10054", "10061", "connection reset", "port unreachable", "no service", "connection refused"]):
        return STATUS_REFUSED, f"{err_str} (ICMP Port Unreachable received)"
    if "timeout" in err_lower or "timed out" in err_lower or "none of the" in err_lower:
        return STATUS_TIMEOUT, f"{err_str} (No response or ICMP received)"
    return STATUS_PROTO_ERROR, err_str


async def do_connect(args, cfg, logger):
    scheme = "coaps" if args.tls else "coap"
    # Connect check: use /.well-known/core or ping
    probe_path = ".well-known/core"
    uri = f"{scheme}://{args.host}:{args.port}/{probe_path}"
    logger.info(f"[*] [CoAP CONNECT CHECK] Sending GET {uri} (DTLS: {args.tls})...")

    try:
        protocol = await Context.create_client_context()
    except Exception as ce:
        logger.error(f"[!] Cannot create aiocoap context: {ce}")
        return format_result("coap", "connect", STATUS_PROTO_ERROR, f"Context error: {ce}", stage="init")

    request = Message(code=Code.GET, uri=uri)
    try:
        resp = await asyncio.wait_for(protocol.request(request).response, timeout=args.timeout)
        raw_code = str(resp.code)
        logger.info(f"[{datetime.now().isoformat()}] <--- Raw CoAP Response: {raw_code}")

        if resp.code in (Code.UNAUTHORIZED, Code.FORBIDDEN):
            await protocol.shutdown()
            return format_result("coap", "connect", STATUS_AUTH_FAILED, f"CoAP {raw_code}", stage="auth")
        elif resp.code == Code.NOT_FOUND:
            await protocol.shutdown()
            # 4.04 means server is alive and responding via CoAP, but path not found
            return format_result("coap", "connect", STATUS_OK, f"CoAP {raw_code} (Server alive, resource not found)", stage="handshake")
        else:
            await protocol.shutdown()
            return format_result("coap", "connect", STATUS_OK, f"CoAP {raw_code}", stage="handshake")

    except asyncio.TimeoutError:
        logger.error(f"[!] CoAP Timeout after {args.timeout}s (Server {args.host}:{args.port} not responding)")
        await protocol.shutdown()
        return format_result("coap", "connect", STATUS_TIMEOUT, f"Timeout after {args.timeout}s (UDP server silent)", stage="handshake")
    except coap_error.NoRequestInterface as nri:
        logger.error(f"[!] CoAP DTLS transport unavailable: {nri}")
        await protocol.shutdown()
        return format_result("coap", "connect", STATUS_PROTO_ERROR,
                             f"DTLS backend not installed for aiocoap on Windows ({nri})", stage="dtls_init")
    except Exception as e:
        await protocol.shutdown()
        err_msg = str(e)
        logger.error(f"[!] CoAP error: {err_msg}")
        status, detail = classify_coap_network_error(err_msg)
        return format_result("coap", "connect", status, detail, stage="handshake")


async def do_send(args, cfg, logger):
    scheme = "coaps" if args.tls else "coap"
    uri = f"{scheme}://{args.host}:{args.port}/{args.path.lstrip('/')}"
    if args.token:
        sep = "&" if "?" in uri else "?"
        uri += f"{sep}token={args.token}"

    logger.info(f"[*] [CoAP SEND] Target: {uri} | Count: {args.count} | Interval: {args.interval}s")
    try:
        protocol = await Context.create_client_context()
    except Exception as ce:
        logger.error(f"[!] Cannot create aiocoap context: {ce}")
        return format_result("coap", "send", STATUS_PROTO_ERROR, f"Context error: {ce}", stage="init")

    success_count = 0
    last_resp = None

    for i in range(1, args.count + 1):
        telemetry = {
            "device_id": args.device_id,
            "seq": i,
            "temperature": round(23.4 + i * 0.4, 2),
            "humidity": round(65.0 - i * 0.3, 2),
            "timestamp": int(time.time() * 1000)
        }
        payload_bytes = json.dumps(telemetry).encode("utf-8")
        request = Message(code=Code.POST, payload=payload_bytes, uri=uri)

        logger.info(f"[{datetime.now().isoformat()}] ---> CoAP POST {uri} (CON) | Payload: {telemetry}")

        try:
            resp = await asyncio.wait_for(protocol.request(request).response, timeout=args.timeout)
            raw_payload = resp.payload.decode("utf-8", errors="replace")
            last_resp = str(resp.code)
            logger.info(f"[{datetime.now().isoformat()}] <--- Raw CoAP Response Code: {resp.code} (MID: {resp.mid})")
            logger.info(f"     Raw CoAP Payload: {raw_payload or '(empty)'}")
            if resp.code.is_successful():
                success_count += 1
            elif resp.code in (Code.UNAUTHORIZED, Code.FORBIDDEN):
                await protocol.shutdown()
                return format_result("coap", "send", STATUS_AUTH_FAILED, f"CoAP {resp.code}", stage="auth")
        except asyncio.TimeoutError:
            logger.error(f"[!] CoAP Request Timeout after {args.timeout}s")
            await protocol.shutdown()
            return format_result("coap", "send", STATUS_TIMEOUT, f"Timeout after {args.timeout}s", stage="request")
        except coap_error.NoRequestInterface as nri:
            logger.error(f"[!] CoAP DTLS transport unavailable: {nri}")
            await protocol.shutdown()
            return format_result("coap", "send", STATUS_PROTO_ERROR,
                                 f"DTLS backend not installed for aiocoap on Windows ({nri})", stage="dtls_init")
        except Exception as e:
            await protocol.shutdown()
            err_msg = str(e)
            logger.error(f"[!] CoAP request error: {err_msg}")
            status, detail = classify_coap_network_error(err_msg)
            return format_result("coap", "send", status, detail, stage="request")

        if i < args.count:
            await asyncio.sleep(args.interval)

    await protocol.shutdown()
    logger.info(f"[*] CoAP send completed: {success_count}/{args.count} successful.")
    if success_count > 0:
        return format_result("coap", "send", STATUS_OK, f"CoAP {last_resp}", stage="channel")
    else:
        return format_result("coap", "send", STATUS_PROTO_ERROR, f"CoAP {last_resp or 'No success responses'}", stage="channel")


async def do_listen(args, cfg, logger):
    scheme = "coaps" if args.tls else "coap"
    uri = f"{scheme}://{args.host}:{args.port}/{args.downlink_path.lstrip('/')}"
    if args.token:
        sep = "&" if "?" in uri else "?"
        uri += f"{sep}token={args.token}"

    logger.info(f"[*] [CoAP LISTEN] Observing downlink at {uri} for {args.duration}s...")
    try:
        protocol = await Context.create_client_context()
    except Exception as ce:
        return format_result("coap", "listen", STATUS_PROTO_ERROR, f"Context error: {ce}", stage="init")

    request = Message(code=Code.GET, uri=uri, observe=0)
    pr = protocol.request(request)

    received_any = False
    start_time = time.time()

    async def observe_loop():
        nonlocal received_any
        try:
            async for resp in pr.observation:
                received_any = True
                payload_str = resp.payload.decode("utf-8", errors="replace")
                logger.info(f"[{datetime.now().isoformat()}] <=== [CoAP OBSERVE DOWNLINK] Code: {resp.code} | Payload: {payload_str}")
        except Exception:
            pass

    task = asyncio.create_task(observe_loop())
    try:
        await asyncio.wait_for(asyncio.sleep(args.duration), timeout=args.duration + 1.0)
    except asyncio.TimeoutError:
        pass
    finally:
        task.cancel()
        await protocol.shutdown()

    elapsed = round(time.time() - start_time, 1)
    if not received_any:
        logger.info(f"[*] Khong nhan duoc ban tin CoAP downlink nao sau {elapsed} giay (Platform chua co CoAP Observe/downlink).")
        return format_result("coap", "listen", STATUS_OK, f"Recv:0 (no downlink received) after {elapsed}s", stage="channel")
    else:
        return format_result("coap", "listen", STATUS_OK, "Recv:1 observe downlink received", stage="channel")


def main():
    cfg = get_default_config()
    parser = argparse.ArgumentParser(
        description="CoAP IoT Protocol Simulator CLI (aiocoap)",
        epilog="Chế độ 'connect' gửi CoAP GET .well-known/core tối thiểu. DTLS qua port 5684."
    )
    parser.add_argument("--mode", choices=["connect", "send", "listen", "both"], default="connect",
                        help="Operating mode: 'connect', 'send', 'listen', or 'both' (default: connect)")
    parser.add_argument("--host", default=cfg["host"], help=f"Target CoAP host (default: {cfg['host']})")
    parser.add_argument("--port", type=int, default=None, help="Target CoAP port (default: 5683 or 5684 if --tls)")
    parser.add_argument("--path", default=cfg["path"], help=f"CoAP URI path (default: {cfg['path']})")
    parser.add_argument("--downlink-path", default=cfg["downlink_path"], help="Downlink observe URI path")
    parser.add_argument("--token", default=cfg["access_token"], help="Device access token")
    parser.add_argument("--tls", action="store_true", default=cfg.get("tls_enabled", False), help="Enable DTLS (coaps://)")
    parser.add_argument("--insecure", action="store_true", default=False, help="Skip certificate verification if DTLS is used")
    parser.add_argument("--device-id", default="sim_coap_001", help="Simulator device ID")
    parser.add_argument("--count", type=int, default=1, help="Telemetry count to send (default: 1)")
    parser.add_argument("--interval", type=float, default=1.0, help="Interval between sends in seconds (default: 1.0)")
    parser.add_argument("--duration", type=float, default=5.0, help="Listen duration in seconds (default: 5.0)")
    parser.add_argument("--timeout", type=float, default=3.0, help="CoAP request timeout in seconds (default: 3.0)")

    args = parser.parse_args()
    logger = setup_logger(args)

    if args.port is None:
        args.port = cfg["port_tls"] if args.tls else cfg["port"]

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    rc = 0
    try:
        if args.mode == "connect":
            rc = loop.run_until_complete(do_connect(args, cfg, logger))
        elif args.mode == "send":
            rc = loop.run_until_complete(do_send(args, cfg, logger))
        elif args.mode == "listen":
            rc = loop.run_until_complete(do_listen(args, cfg, logger))
        else:
            rc_send = loop.run_until_complete(do_send(args, cfg, logger))
            rc_listen = loop.run_until_complete(do_listen(args, cfg, logger))
            rc = rc_send if rc_send != 0 else rc_listen
    finally:
        loop.close()

    sys.exit(rc)


if __name__ == "__main__":
    main()
