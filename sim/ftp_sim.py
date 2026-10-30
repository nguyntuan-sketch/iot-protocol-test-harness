#!/usr/bin/env python3
"""
FTP IoT Protocol Simulator CLI
Protocol #12: FTP Client (ftplib, RFC 959 / RFC 2228 FTPS)
Usage:
  python sim/ftp_sim.py --mode connect
  python sim/ftp_sim.py --mode connect --tls --insecure
  python sim/ftp_sim.py --mode send --allow-write
  python sim/ftp_sim.py --mode listen
  python sim/ftp_sim.py --mode both --allow-write
"""

import os
import sys
import ssl
import time
import ftplib
import hashlib
import io
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
    EXIT_OK,
    EXIT_PROTO_ERROR,
    EXIT_REFUSED,
    EXIT_TIMEOUT,
    EXIT_AUTH_FAILED,
    STATUS_OK,
    STATUS_REFUSED,
    STATUS_TIMEOUT,
    STATUS_AUTH_FAILED,
    STATUS_PROTO_ERROR,
    format_result,
    classify_socket_error,
    tcp_preflight_probe,
    setup_simulator_logger,
    load_merged_config,
    resolve_password_from_file,
    load_payload_config,
    run_devices_parallel,
)


def get_default_config():
    raw = load_merged_config(base_dir)
    target = raw.get("global", {})
    ftp_cfg = raw.get("adapters", {}).get("ftp", {})
    tls_cfg = raw.get("tls", {})
    auth_cfg = ftp_cfg.get("auth", {})

    cfg = {
        "host": target.get("target_host", "127.0.0.1"),
        "port": ftp_cfg.get("port", 21),
        "port_tls": ftp_cfg.get("port_tls", tls_cfg.get("ports", {}).get("ftp_tls", 21)),
        "username": auth_cfg.get("username") or ftp_cfg.get("username", "sim_device_001"),
        "password": auth_cfg.get("password") or ftp_cfg.get("password", "secret"),
        "username_env": auth_cfg.get("username_env", ""),
        "password_env": auth_cfg.get("password_env", ""),
        "password_file": auth_cfg.get("password_file", ""),
        "anonymous": ftp_cfg.get("anonymous", False),
        "remote_file": ftp_cfg.get("remote_filename", "telemetry.txt"),
        "local_file": ftp_cfg.get("local_upload_file", "payloads/sample_tftp_file.txt"),
        "timeout_sec": ftp_cfg.get("timeout_sec", 3.0),
        "tls_enabled": tls_cfg.get("enabled", False),
        "insecure_skip_verify": tls_cfg.get("insecure_skip_verify", True),
    }
    return cfg


def setup_logger(args=None):
    return setup_simulator_logger("ftp", args=args)


def do_send_parallel_ftp(args, cfg, resolved_username, resolved_password, logger):
    t0 = time.perf_counter()
    if not args.allow_write:
        msg = "[stage:preflight] Ghi file bị chặn: Cần cờ --allow-write để thực hiện FTP STOR"
        logger.warning(msg)
        return format_result("ftp", "send", STATUS_PROTO_ERROR, msg, stage="preflight", elapsed=0.0)

    local_path = os.path.join(base_dir, args.local_file) if not os.path.isabs(args.local_file) else args.local_file
    if not os.path.isfile(local_path):
        msg = f"[stage:transfer] Local file not found: {local_path}"
        logger.error(msg)
        return format_result("ftp", "send", STATUS_PROTO_ERROR, msg, stage="transfer", elapsed=0.0)

    with open(local_path, "rb") as fp:
        file_data = fp.read()
    file_size = len(file_data)
    sha256_sent = hashlib.sha256(file_data).hexdigest()

    payload_cfg = load_payload_config("ftp", logger=logger)
    if payload_cfg:
        device_ids = payload_cfg.get("device_ids") or ["sim_ftp_001"]
    else:
        device_ids = ["sim_ftp_001"]

    logger.info(f"[*] [FTP SEND] Target: {args.host}:{args.port} | File: {args.local_file} ({file_size}B) | Devices: {len(device_ids)}")

    def upload_ftp_device(dev_id: str, dev_logger) -> str:
        dev_remote_file = f"{dev_id}_{args.remote_file}"
        dev_logger.info(f"[*] [DEVICE {dev_id}] Connecting to FTP server...")
        dev_ftp = None
        try:
            if args.tls:
                if args.insecure:
                    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                elif args.ca_cert and os.path.isfile(args.ca_cert):
                    ctx = ssl.create_default_context(cafile=args.ca_cert)
                else:
                    ctx = ssl.create_default_context()
                dev_ftp = ftplib.FTP_TLS(context=ctx, timeout=args.timeout)
            else:
                dev_ftp = ftplib.FTP(timeout=args.timeout)

            dev_ftp.connect(args.host, args.port, timeout=args.timeout)
            dev_ftp.set_pasv(not args.active)
            if args.tls and isinstance(dev_ftp, ftplib.FTP_TLS):
                dev_ftp.prot_p()

            if args.anonymous:
                dev_ftp.login(user="anonymous", passwd="anonymous@example.com")
            elif resolved_username:
                dev_ftp.login(user=resolved_username, passwd=resolved_password or "")
            else:
                dev_ftp.login()

            dev_logger.info(f"[{datetime.now().isoformat()}] ---> [DEVICE {dev_id}] Uploading '{dev_remote_file}' ({file_size} bytes)...")
            bio = io.BytesIO(file_data)
            dev_ftp.storbinary(f"STOR {dev_remote_file}", bio)
            try:
                dev_ftp.quit()
            except Exception:
                pass
            return STATUS_OK, f"uploaded {dev_remote_file}"
        finally:
            if dev_ftp:
                try:
                    dev_ftp.close()
                except Exception:
                    pass

    return run_devices_parallel(
        "ftp",
        "send",
        device_ids,
        upload_ftp_device,
        timeout_per_device=args.timeout * (args.count or 1) + 5.0,
        args=args,
        logger=logger,
    )


def main():
    cfg = get_default_config()

    parser = argparse.ArgumentParser(
        description="FTP IoT Protocol Simulator CLI (ftplib, RFC 959 / RFC 2228)",
        epilog="Chế độ 'connect' chỉ bắt tay TCP, đọc banner 220, gửi lệnh đăng nhập và QUIT (không ghi dữ liệu lên server). CẢNH BÁO WINDOWS: trên Windows loopback, cổng đóng có thể mất ~2s mới báo refused (WinError 10061); khuyến nghị dùng --timeout >= 3.0 cho connect.",
    )
    parser.add_argument(
        "--mode",
        choices=["connect", "send", "listen", "both"],
        default="connect",
        help="Operating mode: 'connect', 'send', 'listen', or 'both' (default: connect)",
    )
    parser.add_argument("--host", default=cfg["host"], help=f"FTP server host (default: {cfg['host']})")
    parser.add_argument("--port", type=int, default=None, help="FTP server port (default: 21 or port_tls if --tls)")
    parser.add_argument("--username", default=None, help="FTP username (priority: CLI > env > config)")
    parser.add_argument("--username-env", default=None, help="Environment variable name for username")
    parser.add_argument("--password", default=None, help="FTP password (priority: CLI > env > file > config)")
    parser.add_argument("--password-env", default=None, help="Environment variable name for password")
    parser.add_argument("--password-file", default=None, help="Path to text file containing password")
    parser.add_argument("--anonymous", action="store_true", default=cfg["anonymous"], help="Use anonymous FTP login")
    parser.add_argument("--tls", action="store_true", default=cfg["tls_enabled"], help="Enable Explicit FTPS (FTP_TLS)")
    parser.add_argument("--insecure", action="store_true", default=cfg["insecure_skip_verify"], help="Skip FTPS certificate verification")
    parser.add_argument("--ca-cert", default=None, help="Path to CA certificate for FTPS verification")
    parser.add_argument("--active", action="store_true", default=False, help="Use active data connection mode (default: passive mode)")
    parser.add_argument("--remote-file", default=cfg["remote_file"], help=f"Remote filename (default: {cfg['remote_file']})")
    parser.add_argument("--local-file", default=cfg["local_file"], help=f"Local file to upload (default: {cfg['local_file']})")
    parser.add_argument("--allow-write", action="store_true", default=False, help="Safety barrier: allow STOR write operations")
    parser.add_argument("--count", type=int, default=1, help="Number of files to transfer (default: 1)")
    parser.add_argument("--interval", type=float, default=1.0, help="Interval between transfers in seconds (default: 1.0)")
    parser.add_argument("--duration", type=float, default=5.0, help="Duration for listen mode (default: 5.0)")
    parser.add_argument("--timeout", type=float, default=cfg["timeout_sec"], help=f"Socket timeout in seconds (default: {cfg['timeout_sec']})")

    args = parser.parse_args()

    # Determine port
    if args.port is None:
        args.port = cfg["port_tls"] if args.tls else cfg["port"]

    # Resolve Username: CLI > env > config.auth > config.username
    resolved_username = None
    if args.username is not None:
        resolved_username = args.username
    elif args.username_env:
        resolved_username = os.environ.get(args.username_env, "")
    elif cfg["username_env"]:
        resolved_username = os.environ.get(cfg["username_env"], "")
    else:
        resolved_username = cfg["username"]

    # Resolve Password: CLI > env > file > config.auth > config.password
    resolved_password = None
    if args.password is not None:
        resolved_password = args.password
    elif args.password_env:
        resolved_password = os.environ.get(args.password_env, "")
    elif args.password_file:
        resolved_password = resolve_password_from_file(args.password_file) or ""
    elif cfg["password_env"]:
        resolved_password = os.environ.get(cfg["password_env"], "")
    elif cfg["password_file"]:
        resolved_password = resolve_password_from_file(cfg["password_file"]) or ""
    else:
        resolved_password = cfg["password"]

    logger = setup_logger(args)

    logger.info(f"[{datetime.now().isoformat()}] FTP Simulator starting in '{args.mode}' mode")
    logger.info(f"Target: {args.host}:{args.port} | TLS: {args.tls} | Anonymous: {args.anonymous} | Timeout: {args.timeout}s")
    if not args.anonymous:
        logger.info(f"User: {resolved_username}")

    # Step 1: Pre-flight TCP Connect Probe
    is_connected, probe_status, probe_detail, elapsed = tcp_preflight_probe(args.host, args.port, timeout=args.timeout)
    if not is_connected:
        logger.error(f"[!] Preflight TCP connection failed: {probe_detail}")
        return format_result("ftp", args.mode, probe_status, probe_detail, stage="tcp_connect", elapsed=elapsed)

    if args.mode == "send":
        return do_send_parallel_ftp(args, cfg, resolved_username, resolved_password, logger)

    # Step 2: FTP Connection & Authentication
    t0 = time.perf_counter()
    ftp = None
    banner = ""
    try:
        if args.tls:
            if args.insecure:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            elif args.ca_cert and os.path.isfile(args.ca_cert):
                ctx = ssl.create_default_context(cafile=args.ca_cert)
            else:
                ctx = ssl.create_default_context()
            ftp = ftplib.FTP_TLS(context=ctx, timeout=args.timeout)
        else:
            ftp = ftplib.FTP(timeout=args.timeout)

        ftp.connect(args.host, args.port, timeout=args.timeout)
        banner = ftp.getwelcome() or ""
        logger.info(f"[{datetime.now().isoformat()}] <--- Banner 220: {banner.strip()}")

        # Data connection mode (Passive vs Active)
        ftp.set_pasv(not args.active)

        # Secure data channel if FTPS
        if args.tls and isinstance(ftp, ftplib.FTP_TLS):
            ftp.prot_p()

        # Login
        if args.anonymous:
            logger.info("Logging in as anonymous")
            ftp.login(user="anonymous", passwd="anonymous@example.com")
        elif resolved_username:
            logger.info(f"Logging in with username: {resolved_username}")
            ftp.login(user=resolved_username, passwd=resolved_password or "")
        else:
            logger.info("Attempting login without credentials")
            ftp.login()

        logger.info(f"[{datetime.now().isoformat()}] Authentication successful")

    except ftplib.error_perm as e:
        elapsed = time.perf_counter() - t0
        err_msg = str(e)
        logger.error(f"[!] FTP Permission / Auth error: {err_msg}")
        if "530" in err_msg or "not logged in" in err_msg.lower() or "login incorrect" in err_msg.lower():
            return format_result("ftp", args.mode, STATUS_AUTH_FAILED, f"[stage:login] 530 Login incorrect ({err_msg})", stage="login", elapsed=elapsed)
        return format_result("ftp", args.mode, STATUS_PROTO_ERROR, f"[stage:login] {err_msg}", stage="login", elapsed=elapsed)
    except (ftplib.error_temp, TimeoutError) as e:
        elapsed = time.perf_counter() - t0
        logger.error(f"[!] FTP Timeout: {e}")
        return format_result("ftp", args.mode, STATUS_TIMEOUT, f"[stage:banner] Connection timed out ({e})", stage="banner", elapsed=elapsed)
    except (ConnectionRefusedError, OSError) as e:
        elapsed = time.perf_counter() - t0
        status, detail, _ = classify_socket_error(e, stage="tcp_connect", elapsed=elapsed)
        logger.error(f"[!] FTP Connection failed: {detail}")
        return format_result("ftp", args.mode, status, detail, stage="tcp_connect", elapsed=elapsed)
    except Exception as e:
        elapsed = time.perf_counter() - t0
        logger.error(f"[!] FTP Protocol Error: {e}")
        return format_result("ftp", args.mode, STATUS_PROTO_ERROR, f"[stage:banner] {e}", stage="banner", elapsed=elapsed)

    # Step 3: Handle Mode Operations
    try:
        if args.mode == "connect":
            elapsed = time.perf_counter() - t0
            clean_banner = banner.strip().replace("\r", " ").replace("\n", " ")
            logger.info(f"[{datetime.now().isoformat()}] FTP connect completed successfully (Banner: {clean_banner})")
            try:
                ftp.quit()
            except Exception:
                pass
            return format_result(
                "ftp",
                "connect",
                STATUS_OK,
                f"[stage:banner] Connected and logged in successfully (Banner: {clean_banner})",
                stage="banner",
                elapsed=elapsed,
            )

        elif args.mode == "send":
            # Safety Check: Must have --allow-write
            if not args.allow_write:
                elapsed = time.perf_counter() - t0
                try:
                    ftp.quit()
                except Exception:
                    pass
                msg = "[stage:preflight] Ghi file bị chặn: Cần cờ --allow-write để thực hiện FTP STOR"
                logger.warning(msg)
                return format_result("ftp", "send", STATUS_PROTO_ERROR, msg, stage="preflight", elapsed=elapsed)

            local_path = os.path.join(base_dir, args.local_file) if not os.path.isabs(args.local_file) else args.local_file
            if not os.path.isfile(local_path):
                elapsed = time.perf_counter() - t0
                try:
                    ftp.quit()
                except Exception:
                    pass
                msg = f"[stage:transfer] Local file not found: {local_path}"
                logger.error(msg)
                return format_result("ftp", "send", STATUS_PROTO_ERROR, msg, stage="transfer", elapsed=elapsed)

            with open(local_path, "rb") as fp:
                file_data = fp.read()
            sha256_sent = hashlib.sha256(file_data).hexdigest()
            file_size = len(file_data)

            logger.info(f"Uploading file '{args.remote_file}' ({file_size} bytes, SHA-256: {sha256_sent})")
            bio = io.BytesIO(file_data)
            ftp.storbinary(f"STOR {args.remote_file}", bio)

            # Verification of upload via SIZE or NLST if supported
            verify_info = ""
            try:
                remote_size = ftp.size(args.remote_file)
                if remote_size is not None:
                    verify_info = f" [Verified remote size: {remote_size}B]"
            except Exception:
                try:
                    listing = ftp.nlst()
                    if args.remote_file in listing:
                        verify_info = " [Verified in remote directory listing]"
                except Exception:
                    pass

            elapsed = time.perf_counter() - t0
            try:
                ftp.quit()
            except Exception:
                pass
            return format_result(
                "ftp",
                "send",
                STATUS_OK,
                f"[stage:transfer] File '{args.remote_file}' uploaded successfully ({file_size} bytes, SHA-256: {sha256_sent}){verify_info}",
                stage="transfer",
                elapsed=elapsed,
            )

        elif args.mode == "listen":
            # Download file or list directory
            logger.info(f"Retrieving file '{args.remote_file}' via RETR")
            bio = io.BytesIO()
            try:
                ftp.retrbinary(f"RETR {args.remote_file}", bio.write)
                data = bio.getvalue()
                size = len(data)
                sha256 = hashlib.sha256(data).hexdigest()
                detail = f"[stage:transfer] Downloaded '{args.remote_file}' ({size} bytes, SHA-256: {sha256})"
            except ftplib.error_perm as e:
                # If file not found, fallback to listing
                logger.warning(f"RETR failed ({e}), listing directory via NLST")
                files = ftp.nlst()
                detail = f"[stage:transfer] File '{args.remote_file}' not found. Directory listing ({len(files)} files): {', '.join(files[:5])}"

            elapsed = time.perf_counter() - t0
            try:
                ftp.quit()
            except Exception:
                pass
            return format_result("ftp", "listen", STATUS_OK, detail, stage="transfer", elapsed=elapsed)

        elif args.mode == "both":
            if not args.allow_write:
                elapsed = time.perf_counter() - t0
                try:
                    ftp.quit()
                except Exception:
                    pass
                msg = "[stage:preflight] Ghi file bị chặn: Cần cờ --allow-write để thực hiện FTP STOR"
                logger.warning(msg)
                return format_result("ftp", "both", STATUS_PROTO_ERROR, msg, stage="preflight", elapsed=elapsed)

            local_path = os.path.join(base_dir, args.local_file) if not os.path.isabs(args.local_file) else args.local_file
            with open(local_path, "rb") as fp:
                file_data = fp.read()
            sha256_sent = hashlib.sha256(file_data).hexdigest()
            file_size = len(file_data)

            # 1. Send
            bio = io.BytesIO(file_data)
            ftp.storbinary(f"STOR {args.remote_file}", bio)

            # 2. Listen (Download back and compare hash)
            bio_recv = io.BytesIO()
            ftp.retrbinary(f"RETR {args.remote_file}", bio_recv.write)
            sha256_recv = hashlib.sha256(bio_recv.getvalue()).hexdigest()

            hash_match = "MATCH" if sha256_sent == sha256_recv else "MISMATCH"
            elapsed = time.perf_counter() - t0
            try:
                ftp.quit()
            except Exception:
                pass
            return format_result(
                "ftp",
                "both",
                STATUS_OK,
                f"[stage:transfer] Uploaded and verified '{args.remote_file}' ({file_size} bytes, SHA-256: {sha256_sent}, Integrity: {hash_match})",
                stage="transfer",
                elapsed=elapsed,
            )

    except Exception as e:
        elapsed = time.perf_counter() - t0
        logger.error(f"[!] FTP Operation Exception: {e}")
        return format_result("ftp", args.mode, STATUS_PROTO_ERROR, f"[stage:transfer] {e}", stage="transfer", elapsed=elapsed)
    finally:
        if ftp:
            try:
                ftp.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())

