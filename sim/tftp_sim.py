#!/usr/bin/env python3
"""
TFTP IoT Protocol Simulator CLI
Protocol #11: TFTP Client (tftpy)
Usage:
  python sim/tftp_sim.py --mode connect
  python sim/tftp_sim.py --mode send --file payloads/sample_tftp_file.txt
  python sim/tftp_sim.py --mode listen --duration 5.0
  python sim/tftp_sim.py --mode both
"""

import os
import sys
import time
import socket
import logging
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
    format_result, classify_socket_error, setup_simulator_logger,
    load_payload_config, run_devices_parallel
)

try:
    import yaml
except ImportError:
    yaml = None

try:
    import tftpy
except ImportError:
    print("[ERROR] tftpy not installed. Install with: pip install tftpy", file=sys.stderr)
    sys.exit(1)


def get_default_config():
    cfg_path = os.path.join(base_dir, "config", "config.yaml")
    cfg = {
        "host": "127.0.0.1",
        "port": 69,
        "sample_file": os.path.join(base_dir, "payloads", "sample_tftp_file.txt"),
        "downlink_file": "firmware_update.bin"
    }
    if yaml and os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
                target = raw.get("target", {})
                tf = raw.get("adapters", {}).get("tftp", {})
                cfg["host"] = target.get("host", cfg["host"])
                cfg["port"] = tf.get("port", cfg["port"])
                if "sample_file" in tf:
                    cfg["sample_file"] = os.path.join(base_dir, tf["sample_file"])
        except Exception as e:
            sys.stderr.write(f"[WARN] Failed to read config.yaml: {e}\n")
    return cfg


def setup_logger(args=None):
    return setup_simulator_logger("tftp", args=args)

def do_connect(args, logger):
    logger.info(f"[*] [TFTP CONNECT CHECK] Probing TFTP Server at {args.host}:{args.port} using RRQ...")
    # Opcode 1 (RRQ) + "probe.txt\0" + "octet\0"
    # Socket is UNCONNECTED to allow receiving DATA/ERROR packets from dynamic server TID source ports
    probe_pkt = b'\x00\x01probe.txt\x00octet\x00'
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(args.timeout)

    try:
        sock.sendto(probe_pkt, (args.host, args.port))
        resp, addr = sock.recvfrom(516)
        logger.info(f"[{datetime.now().isoformat()}] <--- Raw TFTP response from TID {addr}: {resp.hex()} ({len(resp)} bytes)")

        # Check opcode: 3 (DATA) or 5 (ERROR)
        if len(resp) >= 4:
            opcode = (resp[0] << 8) | resp[1]
            if opcode == 3:
                block_no = (resp[2] << 8) | resp[3]
                sock.close()
                return format_result("tftp", "connect", STATUS_OK,
                                     f"TFTP DATA received from TID {addr} (block {block_no})", stage="handshake")
            elif opcode == 5:
                err_code = (resp[2] << 8) | resp[3]
                err_msg = resp[4:].decode('latin1', errors='replace').rstrip('\x00')
                sock.close()
                return format_result("tftp", "connect", STATUS_OK,
                                     f"TFTP ERROR received from TID {addr} (Code {err_code}: '{err_msg}') - server alive", stage="handshake")
            else:
                sock.close()
                return format_result("tftp", "connect", STATUS_OK,
                                     f"TFTP response received from TID {addr} (Opcode {opcode})", stage="handshake")
        else:
            sock.close()
            return format_result("tftp", "connect", STATUS_PROTO_ERROR, f"Malformed response: {resp.hex()}", stage="handshake")

    except socket.timeout:
        sock.close()
        logger.error(f"[!] TFTP probe timeout after {args.timeout}s (Server {args.host}:{args.port} silent)")
        return format_result("tftp", "connect", STATUS_TIMEOUT,
                             f"Timeout after {args.timeout}s waiting for RRQ response (server silent)", stage="handshake")
    except (ConnectionResetError, OSError) as e:
        sock.close()
        status, detail, code = classify_socket_error(e, stage="udp_probe")
        logger.error(f"[!] TFTP server unreachable / ICMP port unreachable: {e}")
        return format_result("tftp", "connect", status, detail)
    except Exception as e:
        sock.close()
        logger.error(f"[!] TFTP probe error: {e}")
        return format_result("tftp", "connect", STATUS_PROTO_ERROR, f"Probe error: {e}", stage="handshake")


def do_upload(args, logger):
    if not os.path.isfile(args.file):
        logger.error(f"[!] Upload file not found: {args.file}")
        return format_result("tftp", "send", STATUS_PROTO_ERROR, f"File not found: {args.file}", stage="init")

    payload_cfg = load_payload_config("tftp", logger=logger)
    if payload_cfg:
        device_ids = payload_cfg.get("device_ids") or ["sim_tftp_001"]
    else:
        device_ids = ["sim_tftp_001"]

    file_size = os.path.getsize(args.file)
    logger.info(f"[*] [TFTP UPLOAD] Target: {args.host}:{args.port} | File: {args.file} ({file_size}B) | Devices: {len(device_ids)}")

    def upload_tftp_device(dev_id: str, dev_logger) -> str:
        remote_filename = f"{dev_id}_{os.path.basename(args.file)}"
        dev_logger.info(f"[*] [DEVICE {dev_id}] Uploading {args.file} as '{remote_filename}'...")
        client = tftpy.TftpClient(args.host, args.port, flock=False)
        t0 = time.time()
        client.upload(remote_filename, args.file)
        elapsed = round(time.time() - t0, 3)
        dev_logger.info(f"[{datetime.now().isoformat()}] <--- [DEVICE {dev_id}] [TFTP UPLOAD OK] Transferred {file_size} bytes in {elapsed}s to {remote_filename}")
        return STATUS_OK, f"{file_size}B in {elapsed}s"

    return run_devices_parallel(
        "tftp",
        "send",
        device_ids,
        upload_tftp_device,
        timeout_per_device=args.timeout + 5.0,
        args=args,
        logger=logger,
    )


def do_download_listen(args, logger):
    logger.info(f"[*] [TFTP LISTEN / DOWNLOAD] Checking downlink file '{args.downlink_file}' from {args.host}:{args.port}...")
    client = tftpy.TftpClient(args.host, args.port, flock=False)
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_local = os.path.join(base_dir, "logs", f"downloaded_{args.downlink_file}")

    try:
        client.download(args.downlink_file, target_local)
        dl_size = os.path.getsize(target_local)
        logger.info(f"[{datetime.now().isoformat()}] <=== [TFTP DOWNLINK FILE RECEIVED] Size: {dl_size} bytes saved to {target_local}")
        return format_result("tftp", "listen", STATUS_OK, f"File downloaded ({dl_size} bytes)", stage="channel")
    except tftpy.TftpFileNotFound:
        logger.info(f"[*] File '{args.downlink_file}' khong ton tai tren TFTP server sau {args.duration}s (Platform chua upload downlink file).")
        return format_result("tftp", "listen", STATUS_OK, f"Recv:0 (no downlink received) after {args.duration}s - File not on server", stage="channel")
    except Exception as e:
        logger.info(f"[*] Khong nhan duoc ban tin/file TFTP downlink nao sau {args.duration} giay: {e}")
        return format_result("tftp", "listen", STATUS_OK, f"Recv:0 (no downlink received) after {args.duration}s: {e}", stage="channel")


def main():
    cfg = get_default_config()
    parser = argparse.ArgumentParser(
        description="TFTP IoT Protocol Simulator CLI (tftpy)",
        epilog="Chế độ 'connect' gửi probe RRQ (unconnected socket) để kiểm tra TFTP daemon phản hồi từ TID khác. Không hỗ trợ TLS. Lưu ý: TFTP không có xác thực ở tầng giao thức (RFC 1350)."
    )
    parser.add_argument("--mode", choices=["connect", "send", "listen", "both"], default="connect",
                        help="Operating mode: 'connect', 'send' (upload), 'listen' (download), or 'both' (default: connect)")
    parser.add_argument("--host", default=cfg["host"], help=f"TFTP Server host (default: {cfg['host']})")
    parser.add_argument("--port", type=int, default=cfg["port"], help=f"TFTP Server UDP port (default: {cfg['port']})")
    parser.add_argument("--file", default=cfg["sample_file"], help=f"File to upload (default: {cfg['sample_file']})")
    parser.add_argument("--downlink-file", default=cfg["downlink_file"], help="Downlink file name to fetch")
    parser.add_argument("--tls", action="store_true", help="Request TLS (TFTP RFC 1350 does not define TLS layer)")
    parser.add_argument("--insecure", action="store_true", help="Unused for TFTP")
    parser.add_argument("--duration", type=float, default=5.0, help="Listen / timeout duration in seconds (default: 5.0)")
    parser.add_argument("--timeout", type=float, default=3.0, help="Connection probe timeout in seconds (default: 3.0)")

    args = parser.parse_args()
    logger = setup_logger(args)

    if args.tls:
        logger.warning("[!] TLS not supported in this simulator (TFTP RFC 1350 does not define TLS layer)")

    if args.mode == "connect":
        rc = do_connect(args, logger)
    elif args.mode == "send":
        rc = do_upload(args, logger)
    elif args.mode == "listen":
        rc = do_download_listen(args, logger)
    else:
        rc_up = do_upload(args, logger)
        rc_dl = do_download_listen(args, logger)
        rc = rc_up if rc_up != 0 else rc_dl

    sys.exit(rc)


if __name__ == "__main__":
    main()
