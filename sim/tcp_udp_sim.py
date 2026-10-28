#!/usr/bin/env python3
"""
Raw TCP & UDP IoT Protocol Simulator CLI
Protocol #10: Raw Socket Communication
Usage:
  python sim/tcp_udp_sim.py --transport tcp --mode connect
  python sim/tcp_udp_sim.py --transport udp --mode connect
  python sim/tcp_udp_sim.py --transport tcp --mode both --count 3 --duration 8
  python sim/tcp_udp_sim.py --transport udp --mode both --count 3 --duration 8
  python sim/tcp_udp_sim.py --transport tcp --mode send --count 5
"""

import os
import sys
import time
import json
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
    format_result, classify_socket_error
)

try:
    import yaml
except ImportError:
    yaml = None


def get_default_config():
    cfg_path = os.path.join(base_dir, "config", "config.yaml")
    cfg = {
        "host": "127.0.0.1",
        "tcp_port": 9000,
        "udp_port": 9001
    }
    if yaml and os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
                target = raw.get("target", {})
                tu = raw.get("adapters", {}).get("tcp_udp", {})
                cfg["host"] = target.get("host", cfg["host"])
                cfg["tcp_port"] = tu.get("tcp_port", cfg["tcp_port"])
                cfg["udp_port"] = tu.get("udp_port", cfg["udp_port"])
        except Exception as e:
            sys.stderr.write(f"[WARN] Failed to read config.yaml: {e}\n")
    return cfg


def setup_logger():
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "tcp_udp.log")

    logger = logging.getLogger("tcp_udp_sim")
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


def run_tcp(args, logger):
    logger.info(f"[*] Starting TCP Simulator in '{args.mode}' mode to {args.host}:{args.port}...")
    t0 = time.perf_counter()
    s = socket.socket(socket.AF_INET6 if ":" in args.host else socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(args.timeout)

    try:
        s.connect((args.host, args.port))
        elapsed = time.perf_counter() - t0
        logger.info(f"[{datetime.now().isoformat()}] <--- [TCP CONNECTED] Connection established to {args.host}:{args.port} (elapsed={elapsed:.2f}s)")
    except (ConnectionRefusedError, OSError) as e:
        elapsed = time.perf_counter() - t0
        s.close()
        status, detail, code = classify_socket_error(e, stage="tcp_connect", elapsed=elapsed)
        logger.error(f"[!] Cannot connect via TCP to {args.host}:{args.port}: {e} (elapsed={elapsed:.2f}s)")
        return format_result("tcp_udp", args.mode, status, detail, elapsed=elapsed)
    except Exception as e:
        elapsed = time.perf_counter() - t0
        s.close()
        status, detail, code = classify_socket_error(e, stage="tcp_connect", elapsed=elapsed)
        logger.error(f"[!] TCP connection error: {e} (elapsed={elapsed:.2f}s)")
        return format_result("tcp_udp", args.mode, status, detail, elapsed=elapsed)

    # Mode: CONNECT only
    if args.mode == "connect":
        s.close()
        logger.info("[*] TCP handshake successful. Exiting connect mode.")
        return format_result("tcp_udp", "connect", STATUS_OK,
                             f"TCP connection established to {args.host}:{args.port} (TCP handshake level only)",
                             stage="tcp_connect")

    send_count = 0
    recv_count = 0

    # SEND or BOTH
    if args.mode in ["send", "both"]:
        logger.info(f"[*] Sending {args.count} TCP telemetry packet(s)...")
        for i in range(1, args.count + 1):
            telemetry = {
                "device_id": args.device_id,
                "proto": "TCP",
                "seq": i,
                "temperature": round(25.0 + i * 0.1, 2),
                "timestamp": int(time.time() * 1000)
            }
            data_bytes = (json.dumps(telemetry) + "\n").encode('utf-8')
            try:
                s.sendall(data_bytes)
                logger.info(f"[{datetime.now().isoformat()}] ---> TCP SEND ({len(data_bytes)} bytes): {data_bytes.decode('utf-8').strip()}")
                send_count += 1

                s.settimeout(1.0)
                try:
                    resp = s.recv(1024)
                    if resp:
                        recv_count += 1
                        logger.info(f"[{datetime.now().isoformat()}] <--- Raw TCP Response: hex={resp.hex()} | ascii={resp.decode('utf-8', errors='replace').strip()}")
                except socket.timeout:
                    pass
            except Exception as se:
                logger.error(f"[!] TCP send error: {se}")
                break

            if i < args.count:
                time.sleep(args.interval)

    # LISTEN or BOTH
    if args.mode in ["listen", "both"]:
        logger.info(f"[*] Listening for TCP downlinks for {args.duration}s...")
        s.settimeout(1.0)
        t_start = time.time()
        while time.time() - t_start < args.duration:
            try:
                data = s.recv(2048)
                if not data:
                    logger.info("[*] Server closed TCP connection.")
                    break
                recv_count += 1
                logger.info(f"[{datetime.now().isoformat()}] <=== [TCP DOWNLINK] hex={data.hex()} | ascii={data.decode('utf-8', errors='replace').strip()}")
                ack = b'{"status":"ACK"}\n'
                s.sendall(ack)
                logger.info(f"[{datetime.now().isoformat()}] ===> [AUTO TCP ACK SENT]")
            except socket.timeout:
                pass
            except (ConnectionResetError, ConnectionAbortedError):
                logger.info("[*] Server closed TCP connection.")
                break

        if recv_count == 0:
            logger.info(f"[*] Khong nhan duoc ban tin TCP downlink nao sau {args.duration} giay.")

    s.close()
    logger.info(f"[*] TCP Simulator finished. (Sent: {send_count}, Recv: {recv_count})")
    if args.mode == "listen":
        if recv_count == 0:
            detail = f"Recv:0 (no downlink received) after {args.duration}s"
        else:
            detail = f"Recv:{recv_count} downlink(s) received"
    elif args.mode == "both":
        if recv_count == 0:
            detail = f"Sent:{send_count}, Recv:0 (no downlink received)"
        else:
            detail = f"Sent:{send_count}, Recv:{recv_count}"
    else:
        detail = f"Sent:{send_count}, Recv:{recv_count}"
    return format_result("tcp_udp", args.mode, STATUS_OK, detail, stage="channel")


def run_udp(args, logger):
    logger.info(f"[*] Starting UDP Simulator in '{args.mode}' mode to {args.host}:{args.port}...")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(args.timeout)

    # Bind for LISTEN or BOTH if bind-port is specified (or fallback to port in listen mode)
    bind_port = args.bind_port if args.bind_port is not None else (args.port if args.mode == "listen" else None)
    if bind_port is not None and args.mode in ["listen", "both"]:
        try:
            s.bind((args.bind_host, bind_port))
            logger.info(f"[*] Bound UDP socket to {args.bind_host}:{bind_port}")
        except Exception as be:
            logger.error(f"[!] Cannot bind UDP socket to {args.bind_host}:{bind_port}: {be}")
            s.close()
            return format_result("tcp_udp", args.mode, STATUS_PROTO_ERROR, f"Cannot bind to {args.bind_host}:{bind_port}: {be}", stage="bind")

    # Mode: CONNECT only (Sends UDP probe and checks for response or ICMP error)
    if args.mode == "connect":
        probe_packet = b'{"ping":1}\n'
        t0 = time.perf_counter()
        try:
            s.sendto(probe_packet, (args.host, args.port))
            logger.info(f"[{datetime.now().isoformat()}] ---> UDP probe sent ({len(probe_packet)} bytes) to {args.host}:{args.port}. Awaiting response...")
            # Wait for response or ICMP Port Unreachable [WinError 10054]
            s.settimeout(args.timeout)
            resp, addr = s.recvfrom(1024)
            elapsed = time.perf_counter() - t0
            logger.info(f"[{datetime.now().isoformat()}] <--- Raw UDP response from {addr}: {resp.hex()} (elapsed={elapsed:.2f}s)")
            s.close()
            return format_result("tcp_udp", "connect", STATUS_OK,
                                 f"UDP response received from {addr}: {resp.hex()}",
                                 stage="udp_probe", elapsed=elapsed)
        except (ConnectionResetError, OSError) as e:
            elapsed = time.perf_counter() - t0
            s.close()
            status, detail, code = classify_socket_error(e, stage="udp_probe", elapsed=elapsed)
            logger.error(f"[!] UDP probe rejected / ICMP port unreachable: {e} (elapsed={elapsed:.2f}s)")
            return format_result("tcp_udp", "connect", status, detail, elapsed=elapsed)
        except socket.timeout:
            elapsed = time.perf_counter() - t0
            s.close()
            logger.error(f"[!] UDP probe timeout after {args.timeout}s (UDP im lặng là mơ hồ, không có phản hồi)")
            return format_result("tcp_udp", "connect", STATUS_TIMEOUT,
                                 f"Timeout after {args.timeout}s waiting for UDP response (UDP im lặng là mơ hồ, server có thể không phản hồi hoặc không có tiến trình nghe)",
                                 stage="udp_probe", elapsed=elapsed)
        except Exception as e:
            elapsed = time.perf_counter() - t0
            s.close()
            logger.error(f"[!] UDP connect error: {e}")
            return format_result("tcp_udp", "connect", STATUS_PROTO_ERROR, f"UDP error: {e}", stage="udp_probe", elapsed=elapsed)

    send_count = 0
    recv_count = 0

    # SEND or BOTH
    if args.mode in ["send", "both"]:
        logger.info(f"[*] Sending {args.count} UDP datagram(s)...")
        for i in range(1, args.count + 1):
            telemetry = {
                "device_id": args.device_id,
                "proto": "UDP",
                "seq": i,
                "temperature": round(26.0 + i * 0.2, 2),
                "timestamp": int(time.time() * 1000)
            }
            data_bytes = json.dumps(telemetry).encode('utf-8')
            try:
                s.sendto(data_bytes, (args.host, args.port))
                logger.info(f"[{datetime.now().isoformat()}] ---> UDP SEND ({len(data_bytes)} bytes): {data_bytes.decode('utf-8')}")
                send_count += 1

                s.settimeout(1.0)
                try:
                    resp, addr = s.recvfrom(2048)
                    recv_count += 1
                    logger.info(f"[{datetime.now().isoformat()}] <--- Raw UDP Response from {addr}: hex={resp.hex()} | ascii={resp.decode('utf-8', errors='replace')}")
                except socket.timeout:
                    pass
            except Exception as se:
                logger.error(f"[!] UDP send error: {se}")
                break

            if i < args.count:
                time.sleep(args.interval)

    # LISTEN or BOTH
    if args.mode in ["listen", "both"]:
        bind_info = f" on {args.bind_host}:{bind_port}" if bind_port else ""
        logger.info(f"[*] Listening for UDP downlink datagrams for {args.duration}s{bind_info}...")
        s.settimeout(1.0)
        t_start = time.time()
        while time.time() - t_start < args.duration:
            try:
                data, addr = s.recvfrom(2048)
                recv_count += 1
                hex_str = data.hex()
                ascii_str = data.decode('utf-8', errors='replace').strip()
                logger.info(f"[{datetime.now().isoformat()}] <=== [UDP DOWNLINK from {addr[0]}:{addr[1]}] hex={hex_str} | ascii={ascii_str}")
                ack = b'{"status":"ACK"}\n'
                s.sendto(ack, addr)
                logger.info(f"[{datetime.now().isoformat()}] ===> [AUTO UDP ACK SENT] to {addr[0]}:{addr[1]}")
            except socket.timeout:
                pass
            except (ConnectionResetError, ConnectionAbortedError):
                pass

        if recv_count == 0:
            logger.info(f"[*] Khong nhan duoc datagram UDP downlink nao sau {args.duration} giay.")

    s.close()
    logger.info(f"[*] UDP Simulator finished. (Sent: {send_count}, Recv: {recv_count})")
    if args.mode == "listen":
        if recv_count == 0:
            detail = f"Recv:0 (no downlink received) after {args.duration}s"
        else:
            detail = f"Recv:{recv_count} datagram(s) received"
    elif args.mode == "both":
        if recv_count == 0:
            detail = f"Sent:{send_count}, Recv:0 (no downlink received)"
        else:
            detail = f"Sent:{send_count}, Recv:{recv_count}"
    else:
        detail = f"Sent:{send_count}, Recv:{recv_count}"
    return format_result("tcp_udp", args.mode, STATUS_OK, detail, stage="channel")


def main():
    cfg = get_default_config()
    parser = argparse.ArgumentParser(
        description="Raw TCP & UDP IoT Protocol Simulator CLI",
        epilog="Chế độ 'connect': TCP bắt tay connect; UDP gửi datagram và chờ phản hồi. CẢNH BÁO WINDOWS: trên Windows loopback, cổng đóng có thể mất ~2s mới báo refused (WinError 10061); khuyến nghị dùng --timeout >= 3.0 cho connect."
    )
    parser.add_argument("--transport", choices=["tcp", "udp"], default="tcp", help="Transport protocol: 'tcp' or 'udp' (default: tcp)")
    parser.add_argument("--mode", choices=["connect", "send", "listen", "both"], default="connect",
                        help="Operating mode: 'connect', 'send', 'listen', or 'both' (default: connect)")
    parser.add_argument("--host", default=cfg["host"], help=f"Target host (default: {cfg['host']})")
    parser.add_argument("--port", type=int, default=None, help="Target port (default: 9000 for TCP, 9001 for UDP)")
    parser.add_argument("--bind-host", default="0.0.0.0", help="Local host to bind for UDP listen/both (default: 0.0.0.0)")
    parser.add_argument("--bind-port", type=int, default=None, help="Local port to bind for UDP listen/both")
    parser.add_argument("--device-id", default="sim_raw_001", help="Device ID")
    parser.add_argument("--tls", action="store_true", help="Request TLS (Not supported for raw socket)")
    parser.add_argument("--insecure", action="store_true", help="Unused for raw socket")
    parser.add_argument("--count", type=int, default=1, help="Message count to send (default: 1)")
    parser.add_argument("--interval", type=float, default=1.0, help="Interval between sends in seconds (default: 1.0)")
    parser.add_argument("--duration", type=float, default=5.0, help="Listen duration in seconds (default: 5.0)")
    parser.add_argument("--timeout", type=float, default=3.0, help="Socket timeout in seconds (default: 3.0)")

    args = parser.parse_args()
    logger = setup_logger()

    if args.tls:
        logger.warning("[!] TLS not supported in this simulator (raw sockets only)")

    if args.port is None:
        args.port = cfg["tcp_port"] if args.transport == "tcp" else cfg["udp_port"]

    if args.transport == "tcp":
        rc = run_tcp(args, logger)
    else:
        rc = run_udp(args, logger)

    sys.exit(rc)


if __name__ == "__main__":
    main()
