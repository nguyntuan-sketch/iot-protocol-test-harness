# ===========================================================
# LoRaWAN Semtech UDP Packet Forwarder Simulator (Pure Python)
# File: lorawan_udp_forwarder.py
# Protocol: #8 LoRaWAN - Replaces Docker/LWN-Simulator
# Spec: Semtech UDP Packet Forwarder Protocol v2
# Ref:  https://github.com/Lora-net/packet_forwarder/blob/master/PROTOCOL.TXT
#
# Usage:
#   python lorawan_udp_forwarder.py [--host HOST] [--port PORT] [--count N]
#
# Pass Criteria:
#   Adapter responds with PUSH_ACK (0x01) matching the 2-byte Random Token
# ===========================================================

import socket
import struct
import json
import base64
import random
import time
import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [LoRaWAN-Forwarder] %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/lorawan_forwarder.log", encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)

# --------------- Semtech UDP Protocol Constants ---------------
PROTOCOL_VERSION  = 0x02
PKFWD_PUSH_DATA   = 0x00   # Gateway -> Server
PKFWD_PUSH_ACK    = 0x01   # Server  -> Gateway (expected response)
PKFWD_PULL_DATA   = 0x02   # Gateway -> Server
PKFWD_PULL_RESP   = 0x03   # Server  -> Gateway
PKFWD_PULL_ACK    = 0x04   # Server  -> Gateway
PKFWD_TX_ACK      = 0x05   # Gateway -> Server

# 8-byte Gateway EUI (simulated hardware address)
GATEWAY_EUI = bytes.fromhex("AA555A0000000101")

def build_push_data_packet(token_msb: int, token_lsb: int, rxpk_payload: dict) -> bytes:
    """
    Build a Semtech PUSH_DATA UDP datagram.
    
    Binary header (12 bytes):
      Byte 0:    Protocol Version  (0x02)
      Byte 1:    Random Token MSB
      Byte 2:    Random Token LSB
      Byte 3:    Identifier        (0x00 = PUSH_DATA)
      Bytes 4-11: Gateway EUI      (8 bytes, little-endian)
    
    Followed by JSON payload: {"rxpk": [...]}
    """
    header = struct.pack(
        ">BBBB8s",
        PROTOCOL_VERSION,
        token_msb,
        token_lsb,
        PKFWD_PUSH_DATA,
        GATEWAY_EUI,
    )
    json_payload = json.dumps(rxpk_payload, separators=(",", ":")).encode("utf-8")
    return header + json_payload


def build_sample_rxpk(timestamp_us: int = None) -> dict:
    """Build a sample LoRaWAN uplink rxpk JSON structure."""
    if timestamp_us is None:
        timestamp_us = int(time.monotonic() * 1_000_000) & 0xFFFFFFFF

    # Minimal LoRaWAN MAC frame (Unconfirmed Uplink)
    # MHDR=0x40 | DevAddr=00000001 | FCtrl=0x80 | FCnt=0x0000 | FPort=0x01 | FRMPayload
    frm_payload = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D])
    mac_frame = bytes([0x40, 0x01, 0x00, 0x00, 0x00, 0x80, 0x00, 0x00, 0x01]) + frm_payload
    data_b64 = base64.b64encode(mac_frame).decode("ascii")

    return {
        "rxpk": [
            {
                "tmst": timestamp_us,
                "chan": 0,
                "rfch": 0,
                "freq": 868.1,
                "stat": 1,
                "modu": "LORA",
                "datr": "SF7BW125",
                "codr": "4/5",
                "rssi": -65,
                "lsnr": 9.5,
                "size": len(mac_frame),
                "data": data_b64,
            }
        ]
    }


def send_push_data(host: str, port: int, timeout: float = 5.0) -> dict:
    """
    Send one PUSH_DATA packet and wait for PUSH_ACK.
    Returns result dict with status and latency.
    """
    token_msb = random.randint(0, 0xFF)
    token_lsb = random.randint(0, 0xFF)
    token_hex  = f"0x{token_msb:02X}{token_lsb:02X}"

    rxpk = build_sample_rxpk()
    packet = build_push_data_packet(token_msb, token_lsb, rxpk)

    log.info(f"Sending PUSH_DATA to {host}:{port}  Token={token_hex}  size={len(packet)}B")
    log.debug(f"  rxpk data (B64): {rxpk['rxpk'][0]['data']}")

    result = {
        "host": host,
        "port": port,
        "token": token_hex,
        "packet_size": len(packet),
        "status": "FAIL",
        "latency_ms": None,
        "error": None,
    }

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            t_send = time.monotonic()
            sock.sendto(packet, (host, port))

            ack_data, addr = sock.recvfrom(64)
            t_recv = time.monotonic()
            latency_ms = round((t_recv - t_send) * 1000, 2)

            # Validate PUSH_ACK: version=0x02, token match, identifier=0x01
            if len(ack_data) >= 4:
                ack_ver   = ack_data[0]
                ack_t_msb = ack_data[1]
                ack_t_lsb = ack_data[2]
                ack_id    = ack_data[3]

                if (ack_ver == PROTOCOL_VERSION
                        and ack_id == PKFWD_PUSH_ACK
                        and ack_t_msb == token_msb
                        and ack_t_lsb == token_lsb):
                    result["status"]     = "PASS"
                    result["latency_ms"] = latency_ms
                    log.info(f"  [OK] PUSH_ACK received from {addr}  Token={token_hex}  latency={latency_ms}ms")
                else:
                    result["error"] = (
                        f"ACK mismatch: ver={ack_ver:#04x} id={ack_id:#04x} "
                        f"token={ack_t_msb:02X}{ack_t_lsb:02X} (expected {token_msb:02X}{token_lsb:02X})"
                    )
                    log.error(f"  [!] ACK mismatch: {result['error']}")
            else:
                result["error"] = f"ACK too short: {len(ack_data)} bytes"
                log.error(f"  [!] {result['error']}")

        except socket.timeout:
            result["error"] = "Timeout waiting for PUSH_ACK"
            log.warning(f"  [!] {result['error']} ({timeout}s)")
        except OSError as exc:
            result["error"] = str(exc)
            log.error(f"  [!] Socket error: {exc}")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="LoRaWAN Semtech UDP Packet Forwarder Simulator"
    )
    parser.add_argument("--host",  default="127.0.0.1", help="Target IoT Platform host")
    parser.add_argument("--port",  type=int, default=1700, help="Semtech UDP port (default: 1700)")
    parser.add_argument("--count", type=int, default=3,    help="Number of PUSH_DATA packets to send")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between packets (seconds)")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("LoRaWAN UDP Forwarder Simulator")
    log.info(f"  Target  : {args.host}:{args.port}")
    log.info(f"  GW EUI  : {GATEWAY_EUI.hex().upper()}")
    log.info(f"  Packets : {args.count}  |  Delay: {args.delay}s")
    log.info("=" * 60)

    results = []
    for i in range(args.count):
        log.info(f"[{i+1}/{args.count}] --- Sending uplink ---")
        result = send_push_data(args.host, args.port, timeout=5.0)
        results.append(result)
        if i < args.count - 1:
            time.sleep(args.delay)

    # Summary
    passed = sum(1 for r in results if r["status"] == "PASS")
    log.info("=" * 60)
    log.info(f"SUMMARY: {passed}/{len(results)} packets acknowledged")
    if passed == len(results):
        log.info("[OK] LoRaWAN ADAPTER TEST: COMPLETED")
    else:
        log.warning("[!] LoRaWAN ADAPTER TEST: INCOMPLETE")
    log.info("=" * 60)

    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
