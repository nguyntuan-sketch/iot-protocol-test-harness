#!/usr/bin/env python3
"""
LoRaWAN IoT Protocol Simulator CLI
Protocol #8: LoRaWAN Gateway Simulator (Semtech UDP Forwarder Protocol v2)
Usage:
  python sim/lorawan_sim.py --mode connect --timeout 3
  python sim/lorawan_sim.py --mode both --count 2 --duration 8
  python sim/lorawan_sim.py --mode send --count 3
  python sim/lorawan_sim.py --mode listen --duration 10
"""

import os
import sys
import time
import json
import socket
import struct
import base64
import random
import logging
import argparse
from datetime import datetime, timezone

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
    format_result, classify_socket_error, setup_simulator_logger,
    load_payload_config, generate_payload, run_devices_parallel
)

try:
    import yaml
except ImportError:
    yaml = None

PROTOCOL_VERSION = 0x02
PKT_PUSH_DATA    = 0x00
PKT_PUSH_ACK     = 0x01
PKT_PULL_DATA    = 0x02
PKT_PULL_RESP    = 0x03
PKT_PULL_ACK     = 0x04
PKT_TX_ACK       = 0x05


def get_default_config():
    cfg_path = os.path.join(base_dir, "config", "config.yaml")
    cfg = {
        "host": "127.0.0.1",
        "port": 1700,
        "gateway_eui": "AA555A0000000001",
        "dev_eui": "0004A30B001A2B3C",
        "count": 1,
        "interval": 1.0,
    }
    if yaml and os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
                target = raw.get("target", {})
                lw = raw.get("adapters", {}).get("lorawan", {})
                cfg["host"] = target.get("host", cfg["host"])
                cfg["port"] = lw.get("port", cfg["port"])
                cfg["gateway_eui"] = lw.get("gateway_eui", cfg["gateway_eui"])
                cfg["dev_eui"] = lw.get("dev_eui", cfg["dev_eui"])
        except Exception as e:
            sys.stderr.write(f"[WARN] Failed to read config.yaml: {e}\n")
    try:
        p_cfg = load_payload_config("lorawan")
        if p_cfg:
            if "count" in p_cfg:
                cfg["count"] = p_cfg["count"]
            if "interval" in p_cfg:
                cfg["interval"] = p_cfg["interval"]
    except Exception:
        pass
    return cfg


def setup_logger(args=None):
    return setup_simulator_logger("lorawan", args=args)


def do_send_parallel(args, cfg, gw_eui_bytes, logger):
    payload_cfg = load_payload_config("lorawan", logger=logger)
    if payload_cfg:
        device_ids = payload_cfg.get("device_ids") or [args.dev_eui]
        attributes = payload_cfg.get("attributes", [])
        default_count = payload_cfg.get("count", 1)
        default_interval = payload_cfg.get("interval", 1.0)
    else:
        device_ids = [args.dev_eui]
        attributes = None
        default_count = 1
        default_interval = 1.0

    count = args.count if args.count is not None else default_count
    interval = args.interval if args.interval is not None else default_interval

    logger.info(f"[*] [LoRaWAN SEND] Server: {args.host}:{args.port} | Devices: {len(device_ids)} | Count: {count} | Interval: {interval}s")

    def send_lorawan_device(dev_id: str, dev_logger) -> str:
        dev_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        dev_sock.settimeout(args.timeout)
        push_ack_count = 0
        try:
            for seq in range(1, count + 1):
                token = random.randint(0, 0xFFFF)
                token_msb = (token >> 8) & 0xFF
                token_lsb = token & 0xFF
                header = struct.pack("!BBBB", PROTOCOL_VERSION, token_msb, token_lsb, PKT_PUSH_DATA) + gw_eui_bytes

                if attributes:
                    sensor_data = generate_payload(attributes, device_id=dev_id, seq=seq)
                else:
                    sensor_data = {
                        "device_id": dev_id,
                        "seq": seq,
                        "temp": round(24.5 + seq * 0.2, 2),
                        "humidity": round(60.0 - seq * 0.3, 2),
                        "timestamp": int(time.time() * 1000)
                    }

                dummy_payload = json.dumps(sensor_data).encode('utf-8')
                b64_payload = base64.b64encode(dummy_payload).decode('ascii')
                rxpk = {
                    "rxpk": [{
                        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
                        "tmst": int(time.time() * 1000000) & 0xFFFFFFFF,
                        "chan": 0, "rfch": 0, "freq": 868.100000, "stat": 1,
                        "modu": "LORA", "datr": "SF7BW125", "codr": "4/5", "rssi": -65,
                        "lsnr": 9.2, "size": len(dummy_payload), "data": b64_payload
                    }]
                }
                packet = header + json.dumps(rxpk).encode('utf-8')
                dev_logger.info(f"[{datetime.now().isoformat()}] ---> [DEVICE {dev_id}] PUSH_DATA token=0x{token:04X} ({len(packet)}B) | Sensor: {sensor_data}")
                try:
                    dev_sock.sendto(packet, (args.host, args.port))
                    resp, addr = dev_sock.recvfrom(1024)
                    if len(resp) >= 4 and resp[0] == PROTOCOL_VERSION and resp[3] == PKT_PUSH_ACK:
                        resp_token = (resp[1] << 8) | resp[2]
                        if resp_token == token:
                            dev_logger.info(f"     [PUSH_ACK VERIFIED] token=0x{resp_token:04X}")
                            push_ack_count += 1
                except socket.timeout:
                    dev_logger.warning(f"[!] Device '{dev_id}' PUSH_DATA timeout (No PUSH_ACK from {args.host}:{args.port})")
                except (ConnectionRefusedError, OSError) as ce:
                    dev_logger.error(f"[!] Device '{dev_id}' socket error: {ce}")
                    raise

                if seq < count:
                    time.sleep(interval)
            return STATUS_OK, f"sent {count} packets (ACKs: {push_ack_count})"
        finally:
            dev_sock.close()

    return run_devices_parallel(
        "lorawan",
        "send",
        device_ids,
        send_lorawan_device,
        timeout_per_device=args.timeout * count + 5.0,
        args=args,
        logger=logger,
    )

def main():
    cfg = get_default_config()
    parser = argparse.ArgumentParser(
        description="LoRaWAN IoT Protocol Simulator CLI (Semtech UDP v2 Forwarder)",
        epilog="Chế độ 'connect' gửi PULL_DATA và xác thực token PULL_ACK. Không hỗ trợ TLS. Lưu ý: LoRaWAN không có xác thực ở tầng giao thức (bảo mật nằm trong frame MIC và AppSKey/NwkSKey)."
    )
    parser.add_argument("--mode", choices=["connect", "send", "listen", "both"], default="connect",
                        help="Operating mode: 'connect', 'send', 'listen', or 'both' (default: connect)")
    parser.add_argument("--host", default=cfg["host"], help=f"LoRaWAN Network Server host (default: {cfg['host']})")
    parser.add_argument("--port", type=int, default=cfg["port"], help=f"LoRaWAN UDP port (default: {cfg['port']})")
    parser.add_argument("--gateway-eui", default=cfg["gateway_eui"], help=f"Gateway EUI (default: {cfg['gateway_eui']})")
    parser.add_argument("--dev-eui", default=cfg["dev_eui"], help=f"Device EUI (default: {cfg['dev_eui']})")
    parser.add_argument("--tls", action="store_true", help="Request TLS (Not supported for Semtech UDP)")
    parser.add_argument("--insecure", action="store_true", help="Unused for LoRaWAN")
    parser.add_argument("--count", type=int, default=None, help="Uplink count to send (default: from payload config or 1)")
    parser.add_argument("--interval", type=float, default=None, help="Interval between sends in seconds (default: from payload config or 1.0)")
    parser.add_argument("--duration", type=float, default=5.0, help="Listen duration in seconds (default: 5.0)")
    parser.add_argument("--timeout", type=float, default=3.0, help="UDP ACK timeout in seconds (default: 3.0)")

    args = parser.parse_args()
    logger = setup_logger(args)

    if args.tls:
        logger.warning("[!] TLS not supported in this simulator (Semtech UDP Packet Forwarder protocol v2 does not use TLS)")

    gw_eui_bytes = bytes.fromhex(args.gateway_eui.replace("-", "").replace(":", ""))
    if len(gw_eui_bytes) != 8:
        logger.error(f"[!] Gateway EUI must be 8 bytes (16 hex chars). Got: {args.gateway_eui}")
        return format_result("lorawan", args.mode, STATUS_PROTO_ERROR, f"Invalid Gateway EUI length ({args.gateway_eui})", stage="init")

    if args.mode == "send":
        return do_send_parallel(args, cfg, gw_eui_bytes, logger)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(args.timeout)

    logger.info(f"[*] Starting LoRaWAN Simulator in '{args.mode}' mode...")
    logger.info(f"    Server: {args.host}:{args.port} | Gateway EUI: {args.gateway_eui} | Dev EUI: {args.dev_eui}")

    # Mode: CONNECT only (PULL_DATA probe)
    if args.mode == "connect":
        token = random.randint(0, 0xFFFF)
        token_msb = (token >> 8) & 0xFF
        token_lsb = token & 0xFF
        # Byte 0: version (0x02), Bytes 1-2: token, Byte 3: identifier (0x02 PULL_DATA), Bytes 4-11: EUI (8 bytes) -> 12 bytes
        pull_header = struct.pack("!BBBB", PROTOCOL_VERSION, token_msb, token_lsb, PKT_PULL_DATA) + gw_eui_bytes

        logger.info(f"[{datetime.now().isoformat()}] ---> PULL_DATA probe (token=0x{token:04X}, len={len(pull_header)}B, hex={pull_header.hex()})...")
        try:
            sock.sendto(pull_header, (args.host, args.port))
            resp, addr = sock.recvfrom(1024)
            logger.info(f"[{datetime.now().isoformat()}] <--- Raw response from {addr}: {resp.hex()} ({len(resp)} bytes)")

            # Verify response: len >= 4, version == 0x02, identifier == 0x04 (PULL_ACK), token matches
            if len(resp) >= 4:
                resp_ver = resp[0]
                resp_token = (resp[1] << 8) | resp[2]
                resp_id = resp[3]

                if resp_ver != PROTOCOL_VERSION:
                    sock.close()
                    return format_result("lorawan", "connect", STATUS_PROTO_ERROR,
                                         f"Invalid ACK protocol version 0x{resp_ver:02X} (expected 0x{PROTOCOL_VERSION:02X})", stage="handshake")

                if resp_id != PKT_PULL_ACK:
                    sock.close()
                    return format_result("lorawan", "connect", STATUS_PROTO_ERROR,
                                         f"Unexpected packet identifier 0x{resp_id:02X} (expected 0x{PKT_PULL_ACK:02X} PULL_ACK)", stage="handshake")

                if resp_token != token:
                    sock.close()
                    logger.error(f"[!] Mismatched token in PULL_ACK: got 0x{resp_token:04X}, expected 0x{token:04X}")
                    return format_result("lorawan", "connect", STATUS_PROTO_ERROR,
                                         f"Token mismatch (sent 0x{token:04X}, received 0x{resp_token:04X})", stage="handshake")

                logger.info(f"[*] PULL_ACK verified (token=0x{resp_token:04X}) from {addr}")
                sock.close()
                return format_result("lorawan", "connect", STATUS_OK,
                                     f"PULL_ACK verified token=0x{resp_token:04X} (ACK chi chung minh cong UDP co nguoi nghe, khong chung minh frame duoc giai ma/MIC hop le)",
                                     stage="handshake")
            else:
                sock.close()
                return format_result("lorawan", "connect", STATUS_PROTO_ERROR, f"Malformed response (<4 bytes): {resp.hex()}", stage="handshake")

        except socket.timeout:
            sock.close()
            logger.error(f"[!] PULL_DATA timeout after {args.timeout}s (Server {args.host}:{args.port} silent)")
            return format_result("lorawan", "connect", STATUS_TIMEOUT,
                                 f"Timeout waiting for PULL_ACK after {args.timeout}s (UDP server silent)", stage="handshake")
        except (ConnectionRefusedError, OSError) as e:
            sock.close()
            status, detail, code = classify_socket_error(e, stage="udp_socket")
            logger.error(f"[!] UDP socket error / ICMP received: {e}")
            return format_result("lorawan", "connect", status, detail)

    push_ack_success = 0
    pull_ack_success = 0
    downlink_count = 0

    # SEND: PUSH_DATA
    if args.mode in ["send", "both"]:
        logger.info(f"[*] Sending {args.count} PUSH_DATA uplink packet(s)...")
        for i in range(1, args.count + 1):
            token = random.randint(0, 0xFFFF)
            token_msb = (token >> 8) & 0xFF
            token_lsb = token & 0xFF

            # 12-byte header: byte 0=0x02, bytes 1-2=token, byte 3=0x00 PUSH_DATA, bytes 4-11=GW EUI
            header = struct.pack("!BBBB", PROTOCOL_VERSION, token_msb, token_lsb, PKT_PUSH_DATA) + gw_eui_bytes

            dummy_payload = json.dumps({"temp": 24.5 + i, "humidity": 60 - i}).encode('utf-8')
            b64_payload = base64.b64encode(dummy_payload).decode('ascii')
            rxpk = {
                "rxpk": [{
                    "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
                    "tmst": int(time.time() * 1000000) & 0xFFFFFFFF,
                    "chan": 0, "rfch": 0, "freq": 868.100000, "stat": 1,
                    "modu": "LORA", "datr": "SF7BW125", "codr": "4/5", "rssi": -65,
                    "lsnr": 9.2, "size": len(dummy_payload), "data": b64_payload
                }]
            }
            packet = header + json.dumps(rxpk).encode('utf-8')

            logger.info(f"[{datetime.now().isoformat()}] ---> PUSH_DATA token=0x{token:04X} ({len(packet)}B, header={header.hex()})")
            try:
                sock.sendto(packet, (args.host, args.port))
                resp, addr = sock.recvfrom(1024)
                logger.info(f"[{datetime.now().isoformat()}] <--- Raw Response from {addr}: {resp.hex()} ({len(resp)} bytes)")
                if len(resp) >= 4 and resp[0] == PROTOCOL_VERSION and resp[3] == PKT_PUSH_ACK:
                    resp_token = (resp[1] << 8) | resp[2]
                    if resp_token == token:
                        logger.info(f"     [PUSH_ACK VERIFIED] token=0x{resp_token:04X}")
                        push_ack_success += 1
                    else:
                        logger.warning(f"     [!] PUSH_ACK token mismatch: got 0x{resp_token:04X}, expected 0x{token:04X}")
                else:
                    logger.warning(f"     [!] Unexpected packet received: {resp.hex()}")
            except socket.timeout:
                logger.warning(f"[!] PUSH_DATA timeout after {args.timeout}s (No PUSH_ACK from {args.host}:{args.port})")
            except Exception as e:
                logger.error(f"[!] Socket error during PUSH_DATA: {e}")

            if i < args.count:
                time.sleep(args.interval)

    # LISTEN: PULL_DATA + PULL_RESP
    if args.mode in ["listen", "both"]:
        logger.info(f"[*] Sending PULL_DATA and listening for downlink (PULL_RESP) for {args.duration}s...")
        token = random.randint(0, 0xFFFF)
        token_msb = (token >> 8) & 0xFF
        token_lsb = token & 0xFF
        pull_header = struct.pack("!BBBB", PROTOCOL_VERSION, token_msb, token_lsb, PKT_PULL_DATA) + gw_eui_bytes
        try:
            sock.sendto(pull_header, (args.host, args.port))
            logger.info(f"[{datetime.now().isoformat()}] ---> PULL_DATA token=0x{token:04X} sent (hex={pull_header.hex()}).")
            sock.settimeout(2.0)
            resp, addr = sock.recvfrom(1024)
            if len(resp) >= 4 and resp[3] == PKT_PULL_ACK:
                resp_token = (resp[1] << 8) | resp[2]
                if resp_token == token:
                    logger.info(f"[{datetime.now().isoformat()}] <--- [PULL_ACK VERIFIED] token=0x{resp_token:04X}")
                    pull_ack_success += 1
        except socket.timeout:
            logger.warning(f"[!] PULL_DATA timeout (No PULL_ACK from server)")
        except Exception as e:
            logger.error(f"[!] Error sending PULL_DATA: {e}")

        sock.settimeout(1.0)
        t_start = time.time()
        while time.time() - t_start < args.duration:
            try:
                data, addr = sock.recvfrom(2048)
                if len(data) >= 4 and data[0] == PROTOCOL_VERSION and data[3] == PKT_PULL_RESP:
                    downlink_count += 1
                    t_resp = (data[1] << 8) | data[2]
                    json_str = data[4:].decode('utf-8', errors='replace')
                    logger.info(f"[{datetime.now().isoformat()}] <=== [DOWNLINK PULL_RESP] token=0x{t_resp:04X}")
                    logger.info(f"     Payload: {json_str}")

                    tx_ack = struct.pack("!BBBB", PROTOCOL_VERSION, data[1], data[2], PKT_TX_ACK) + gw_eui_bytes
                    sock.sendto(tx_ack, addr)
                    logger.info(f"[{datetime.now().isoformat()}] ===> [AUTO TX_ACK SENT] token=0x{t_resp:04X} to {addr}")
            except socket.timeout:
                pass

        if downlink_count == 0:
            logger.info(f"[*] Khong nhan duoc ban tin LoRaWAN downlink (PULL_RESP) nao sau {args.duration} giay.")

    sock.close()
    logger.info(f"[*] LoRaWAN Simulator finished. (PUSH_ACK: {push_ack_success}/{args.count}, PULL_ACK: {pull_ack_success}, Downlinks: {downlink_count})")
    if args.mode == "listen":
        if downlink_count == 0:
            detail = f"Recv:0 (no downlink received) after {args.duration}s (PULL_ACK:{pull_ack_success})"
        else:
            detail = f"Recv:{downlink_count} downlink(s) received (PULL_ACK:{pull_ack_success})"
    elif args.mode == "both":
        if downlink_count == 0:
            detail = f"PUSH_ACK:{push_ack_success}/{args.count}, PULL_ACK:{pull_ack_success}, Recv:0 (no downlink received)"
        else:
            detail = f"PUSH_ACK:{push_ack_success}/{args.count}, PULL_ACK:{pull_ack_success}, Recv:{downlink_count}"
    else:
        detail = f"PUSH_ACK:{push_ack_success}/{args.count}, PULL_ACK:{pull_ack_success} (ACK chi chung minh cong UDP co nguoi nghe, khong chung minh frame duoc giai ma/MIC hop le)"

    if push_ack_success > 0 or pull_ack_success > 0 or downlink_count > 0:
        return format_result("lorawan", args.mode, STATUS_OK, detail, stage="channel")
    else:
        return format_result("lorawan", args.mode, STATUS_TIMEOUT, detail, stage="channel")


if __name__ == "__main__":
    sys.exit(main())
