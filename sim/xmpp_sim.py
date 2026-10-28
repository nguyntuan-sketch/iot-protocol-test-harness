#!/usr/bin/env python3
"""
XMPP IoT Protocol Simulator CLI
Protocol #4: XMPP Client (slixmpp async)
Usage:
  python sim/xmpp_sim.py --mode connect
  python sim/xmpp_sim.py --mode connect --tls
  python sim/xmpp_sim.py --mode both --count 2 --duration 8
  python sim/xmpp_sim.py --mode send --count 3
  python sim/xmpp_sim.py --mode listen --duration 10
"""

import os
import sys
import ssl
import time
import json
import socket
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
    format_result, classify_socket_error, tcp_preflight_probe, setup_simulator_logger
)

try:
    import yaml
except ImportError:
    yaml = None

try:
    import slixmpp
    from slixmpp import ClientXMPP
except ImportError:
    print("[ERROR] slixmpp not installed. Install with: pip install slixmpp", file=sys.stderr)
    sys.exit(1)


def get_default_config():
    cfg_path = os.path.join(base_dir, "config", "config.yaml")
    cfg = {
        "host": "127.0.0.1",
        "port": 5222,
        "port_tls": 5223,
        "client_jid": "sim_device_001@iot.local",
        "client_password": "secret",
        "target_adapter_jid": "adapter@iot.local",
        "message_type": "chat",
        "tls_enabled": False,
        "insecure_skip_verify": False
    }
    if yaml and os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
                target = raw.get("target", {})
                xm = raw.get("adapters", {}).get("xmpp", {})
                tls = raw.get("tls", {})
                cfg["host"] = target.get("host", cfg["host"])
                cfg["port"] = xm.get("port", cfg["port"])
                cfg["client_jid"] = xm.get("client_jid", cfg["client_jid"])
                cfg["client_password"] = xm.get("client_password", cfg["client_password"])
                cfg["target_adapter_jid"] = xm.get("target_adapter_jid", cfg["target_adapter_jid"])
                cfg["message_type"] = xm.get("message_type", cfg["message_type"])
                cfg["tls_enabled"] = tls.get("enabled", cfg["tls_enabled"])
                cfg["insecure_skip_verify"] = tls.get("insecure_skip_verify", cfg["insecure_skip_verify"])
                ports = tls.get("ports", {})
                cfg["port_tls"] = ports.get("xmpp_tls", cfg["port_tls"])
        except Exception as e:
            sys.stderr.write(f"[WARN] Failed to read config.yaml: {e}\n")
    return cfg


def setup_logger(args=None):
    return setup_simulator_logger("xmpp", args=args)


class XMPPSimClient(ClientXMPP):
    def __init__(self, jid, password, target_jid, mode, count, interval, duration, logger):
        super().__init__(jid, password)
        self.target_jid = target_jid
        self.mode = mode
        self.count = count
        self.interval = interval
        self.duration = duration
        self.logger = logger

        # Sequence progress tracking
        self.current_stage = "tcp_connect"
        self.session_started = asyncio.Event()
        self.auth_failed = asyncio.Event()
        self.conn_failed = asyncio.Event()
        self.conn_error_detail = ""
        self.messages_received = []
        self.send_success = 0

        self.add_event_handler("connection_failed", self.on_conn_failed)
        self.add_event_handler("session_start", self.on_session_start)
        self.add_event_handler("message", self.on_message)
        self.add_event_handler("failed_auth", self.on_failed_auth)
        self.add_event_handler("disconnected", self.on_disconnected)
        self.add_event_handler("ssl_invalid_cert", self.on_invalid_cert)

    def on_conn_failed(self, event):
        self.conn_error_detail = str(event)
        self.logger.error(f"[!] [stage:{self.current_stage}] Connection failed: {event}")
        self.conn_failed.set()

    def on_invalid_cert(self, pem_cert):
        self.logger.error(f"[!] [stage:starttls] SSL Certificate invalid/untrusted")
        self.conn_error_detail = "Invalid SSL certificate"
        self.conn_failed.set()

    async def on_session_start(self, event):
        self.current_stage = "bind"
        self.logger.info(f"[{datetime.now().isoformat()}] <--- [XMPP AUTH OK] Session bound for {self.boundjid.bare}")
        self.send_presence()
        self.session_started.set()

    def on_failed_auth(self, event):
        self.current_stage = "sasl"
        self.logger.error(f"[!] [XMPP AUTH FAILED] Credentials rejected for {self.boundjid.bare}: {event}")
        self.auth_failed.set()

    def on_disconnected(self, event):
        self.logger.info(f"[*] XMPP disconnected event.")

    def on_message(self, msg):
        if msg['type'] in ('chat', 'normal'):
            body = msg['body']
            from_jid = str(msg['from'])
            self.logger.info(f"[{datetime.now().isoformat()}] <=== [XMPP DOWNLINK] From: {from_jid} | Body: {body}")
            self.messages_received.append((from_jid, body))

            try:
                data = json.loads(body)
                if "cmd" in data or "rpc" in data:
                    ack_body = json.dumps({"status": "ACK", "received_at": int(time.time()*1000)})
                    self.send_message(mto=from_jid, mbody=ack_body, mtype='chat')
                    self.logger.info(f"[{datetime.now().isoformat()}] ===> [AUTO ACK SENT] To: {from_jid} | Body: {ack_body}")
            except Exception:
                pass


async def run_xmpp_sim(args, cfg, logger):
    logger.info(f"[*] Starting XMPP Simulator in '{args.mode}' mode (TLS: {args.tls})...")
    logger.info(f"    Server: {args.host}:{args.port} | JID: {args.jid} | Target JID: {args.target_jid}")

    # Step 1: Pre-flight TCP Connect Check
    # This prevents misclassifying TCP RST (Connection Refused) as TIMEOUT
    is_connected, status, detail, elapsed = tcp_preflight_probe(args.host, args.port, timeout=args.timeout)
    if not is_connected:
        logger.error(f"[!] TCP connect failed to {args.host}:{args.port}: {detail}")
        return format_result("xmpp", args.mode, status, detail, elapsed=elapsed)
    logger.info(f"[*] [stage:tcp_connect] TCP socket connected to {args.host}:{args.port} (elapsed={elapsed:.2f}s)")

    xmpp = XMPPSimClient(
        jid=args.jid,
        password=args.password,
        target_jid=args.target_jid,
        mode=args.mode,
        count=args.count,
        interval=args.interval,
        duration=args.duration,
        logger=logger
    )

    if args.tls:
        # Direct TLS on port 5223
        xmpp.use_ssl = True
        xmpp.use_starttls = False
    else:
        # STARTTLS on port 5222
        xmpp.use_ssl = False
        xmpp.use_starttls = True

    if args.insecure:
        logger.warning("[!] WARNING: TLS certificate verification is disabled (--insecure)")
        xmpp.ssl_context.check_hostname = False
        xmpp.ssl_context.verify_mode = ssl.CERT_NONE

    xmpp.register_plugin('xep_0030')
    xmpp.register_plugin('xep_0199')

    xmpp.current_stage = "stream_open"
    try:
        xmpp.connect((args.host, args.port))
    except Exception as e:
        status, detail, code = classify_socket_error(e, stage=xmpp.current_stage)
        logger.error(f"[!] Error initiating XMPP stream: {e}")
        return format_result("xmpp", args.mode, status, detail)

    # Wait for session_start, failed_auth, or connection failure
    done, pending = await asyncio.wait(
        [
            asyncio.create_task(xmpp.session_started.wait()),
            asyncio.create_task(xmpp.auth_failed.wait()),
            asyncio.create_task(xmpp.conn_failed.wait())
        ],
        timeout=args.timeout,
        return_when=asyncio.FIRST_COMPLETED
    )

    for p in pending:
        p.cancel()

    if xmpp.auth_failed.is_set():
        xmpp.disconnect()
        logger.error(f"[!] SASL authentication failed for JID {args.jid}")
        return format_result("xmpp", args.mode, STATUS_AUTH_FAILED,
                             f"SASL not-authorized / bad credentials for {args.jid}",
                             stage="sasl")

    if xmpp.conn_failed.is_set():
        xmpp.disconnect()
        logger.error(f"[!] Connection failed at stage '{xmpp.current_stage}': {xmpp.conn_error_detail}")
        if "ssl" in xmpp.conn_error_detail.lower() or "cert" in xmpp.conn_error_detail.lower():
            return format_result("xmpp", args.mode, STATUS_AUTH_FAILED,
                                 f"TLS verification failed: {xmpp.conn_error_detail}", stage="starttls")
        else:
            return format_result("xmpp", args.mode, STATUS_PROTO_ERROR,
                                 f"Failed at stage {xmpp.current_stage}: {xmpp.conn_error_detail}", stage=xmpp.current_stage)

    if not xmpp.session_started.is_set():
        xmpp.disconnect()
        logger.error(f"[!] Timeout waiting for XMPP session_start after {args.timeout}s (stopped at stage: {xmpp.current_stage})")
        return format_result("xmpp", args.mode, STATUS_TIMEOUT,
                             f"No stream/SASL response within {args.timeout}s",
                             stage=xmpp.current_stage)

    # Mode: CONNECT only
    if args.mode == "connect":
        xmpp.disconnect()
        logger.info("[*] XMPP handshake & auth successful. Exiting connect mode.")
        return format_result("xmpp", "connect", STATUS_OK, f"Session bound for {args.jid}", stage="bind")

    # Mode: SEND or BOTH
    if args.mode in ["send", "both"]:
        logger.info(f"[*] Sending {args.count} telemetry message(s) to {args.target_jid}...")
        for i in range(1, args.count + 1):
            telemetry = {
                "device_id": args.jid.split('@')[0],
                "seq": i,
                "temperature": round(24.0 + i * 0.2, 2),
                "humidity": round(58.0 - i * 0.5, 2),
                "timestamp": int(time.time() * 1000)
            }
            body = json.dumps(telemetry)
            xmpp.send_message(mto=args.target_jid, mbody=body, mtype='chat')
            logger.info(f"[{datetime.now().isoformat()}] ---> XMPP MESSAGE sent to {args.target_jid}: {body}")
            xmpp.send_success += 1
            if i < args.count:
                await asyncio.sleep(args.interval)

    # Mode: LISTEN or BOTH
    if args.mode in ["listen", "both"]:
        logger.info(f"[*] Listening for XMPP downlink messages for {args.duration}s...")
        await asyncio.sleep(args.duration)
        if len(xmpp.messages_received) == 0:
            logger.info(f"[*] Khong nhan duoc ban tin XMPP downlink nao sau {args.duration} giay.")

    xmpp.disconnect()
    logger.info("[*] XMPP Simulator finished cleanly.")
    if args.mode == "listen":
        if len(xmpp.messages_received) == 0:
            detail = f"Recv:0 (no downlink received) after {args.duration}s"
        else:
            detail = f"Recv:{len(xmpp.messages_received)} message(s) received"
    elif args.mode == "both":
        if len(xmpp.messages_received) == 0:
            detail = f"Sent:{xmpp.send_success}, Recv:0 (no downlink received)"
        else:
            detail = f"Sent:{xmpp.send_success}, Recv:{len(xmpp.messages_received)}"
    else:
        detail = "Completed cleanly"
    return format_result("xmpp", args.mode, STATUS_OK, detail, stage="channel")


def main():
    cfg = get_default_config()
    parser = argparse.ArgumentParser(
        description="XMPP IoT Protocol Simulator CLI (slixmpp)",
        epilog="Chế độ 'connect' qua các giai đoạn: tcp_connect -> stream -> STARTTLS -> SASL -> bind. CẢNH BÁO WINDOWS: trên Windows loopback, cổng đóng có thể mất ~2s mới báo refused (WinError 10061); khuyến nghị dùng --timeout >= 3.0 cho connect."
    )
    parser.add_argument("--mode", choices=["connect", "send", "listen", "both"], default="connect",
                        help="Operating mode: 'connect', 'send', 'listen', or 'both' (default: connect)")
    parser.add_argument("--host", default=cfg["host"], help=f"XMPP Server host (default: {cfg['host']})")
    parser.add_argument("--port", type=int, default=None, help="XMPP Server port (default: 5222 or 5223 if --tls)")
    parser.add_argument("--jid", default=cfg["client_jid"], help=f"Client JID (default: {cfg['client_jid']})")
    parser.add_argument("--password", default=cfg["client_password"], help="Client Password")
    parser.add_argument("--target-jid", default=cfg["target_adapter_jid"], help="Target Adapter JID")
    parser.add_argument("--tls", action="store_true", default=cfg.get("tls_enabled", False), help="Enable XMPP Direct TLS (port 5223)")
    parser.add_argument("--insecure", action="store_true", default=cfg.get("insecure_skip_verify", False), help="Skip TLS certificate verification")
    parser.add_argument("--count", type=int, default=1, help="Message count to send (default: 1)")
    parser.add_argument("--interval", type=float, default=1.0, help="Interval between sends in seconds (default: 1.0)")
    parser.add_argument("--duration", type=float, default=5.0, help="Listening duration in seconds (default: 5.0)")
    parser.add_argument("--timeout", type=float, default=3.0, help="Timeout in seconds (default: 3.0)")

    args = parser.parse_args()
    logger = setup_logger(args)

    if args.port is None:
        args.port = cfg["port_tls"] if args.tls else cfg["port"]

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        rc = loop.run_until_complete(run_xmpp_sim(args, cfg, logger))
    finally:
        loop.close()

    sys.exit(rc)


if __name__ == "__main__":
    main()
