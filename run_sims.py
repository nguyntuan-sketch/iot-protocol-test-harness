#!/usr/bin/env python3
"""
IoT Protocol Simulators Aggregate Runner
File: run_sims.py

Orchestrates sequential execution of all 11 IoT Protocol Simulators in sim/
(12 protocol targets including TCP and UDP separately).
"""

import argparse
import datetime
import os
import sys
from typing import Dict, List, Optional, Set

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Step 1: Strict Environment Check
from runner.execute import check_environment, get_java_pids, execute_cli
check_environment()

import yaml
from runner.registry import (
    CLIEntry,
    get_default_registry,
    build_cli_command,
    parse_custom_args,
    detect_supported_flags,
    print_payload_info,
)
from runner.parse import parse_cli_output, ParsedResult
from runner.report import (
    print_console_table,
    write_results_json,
    write_results_md,
    MANDATORY_NOTICE,
)


def load_config(config_path: str = "config/config.yaml") -> dict:
    """Loads central configuration safely."""
    if not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[CẢNH BÁO] Không thể đọc cấu hình từ {config_path}: {e}", file=sys.stderr)
        return {}


def parse_args():
    parser = argparse.ArgumentParser(
        description="IoT Protocol Simulators Runner — Chạy gộp 11 CLI trong sim/",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ví dụ sử dụng:
  python run_sims.py --dry-run
  python run_sims.py --mode connect
  python run_sims.py --only mqtt,tcp --timeout 3
  python run_sims.py --mode send --only http --yes
  python run_sims.py --arg mqtt="--port 1884"
""",
    )

    parser.add_argument(
        "--mode",
        choices=["connect", "send", "listen", "both"],
        default="connect",
        help="Chế độ chạy cho các simulator (mặc định: connect)",
    )
    parser.add_argument(
        "--only",
        type=str,
        default="",
        help="Danh sách các giao thức cần chạy, ngăn cách bởi dấu phẩy (vd: mqtt,tcp)",
    )
    parser.add_argument(
        "--skip",
        type=str,
        default="",
        help="Danh sách các giao thức bỏ qua, ngăn cách bởi dấu phẩy (vd: dds,lorawan)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="",
        help="Host platform đích (mặc định đọc từ config/config.yaml: global.target_host)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Timeout (giây) cho các CLI (mặc định: 5.0; connect tự nâng tối thiểu 3.0s)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Số lượng bản tin telemetry gửi (cho send/both)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Khoảng cách giữa các lần gửi (giây)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Thời gian lắng nghe downlink (giây cho listen/both)",
    )
    parser.add_argument(
        "--arg",
        action="append",
        dest="custom_args",
        metavar='PROTO="ARGUMENTS"',
        help='Bổ sung tham số thô cho từng CLI cụ thể (vd: --arg mqtt="--port 1884")',
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chỉ in dòng lệnh sẽ thực thi cho từng simulator, không chạy thật",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Bắt buộc xác nhận khi chạy chế độ có tác động gửi mạng ('send' hoặc 'both')",
    )
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help="Cho phép TFTP ghi file lên server (bắt buộc khi chạy tftp ở chế độ send/both)",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default="",
        help="Thư mục ghi log và báo cáo kết quả (mặc định: logs/runs/<timestamp>)",
    )
    parser.add_argument(
        "--show-payload",
        action="store_true",
        help="In cấu trúc bản tin và payload mẫu gửi đi của từng simulator ra console và lưu vào báo cáo",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        dest="show_payload",
        help="Tương đương --show-payload",
    )
    parser.add_argument(
        "--grace",
        type=float,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--custom-script",
        type=str,
        default="",
        help=argparse.SUPPRESS,
    )

    return parser.parse_args()


def main():
    args = parse_args()
    config = load_config("config/config.yaml")

    # Target host determination & Advisory
    target_host = args.host if args.host else config.get("global", {}).get("target_host", "127.0.0.1")
    if target_host.lower() == "localhost":
        print("[CẢNH BÁO] Host hiện tại là 'localhost'. Khuyên dùng '127.0.0.1' để tránh Windows thử IPv6 (::1) gây trễ thêm ~2s khi cổng đóng.", file=sys.stderr)

    # Port 8080 Advisory
    adapters_cfg = config.get("adapters", {})
    http_port = adapters_cfg.get("http", {}).get("port", 8080)
    ws_port = adapters_cfg.get("websocket", {}).get("port", 8080)
    if http_port == 8080 or ws_port == 8080:
        print("[LƯU Ý] Cổng 8080 (HTTP/WebSocket) thường bị dịch vụ khác chiếm. Hãy xác nhận đúng platform của bạn bằng: netstat -ano | findstr :8080. Kết quả tại cổng này có thể không phải của IoT Platform.", file=sys.stderr)

    # Connect mode timeout clamping
    if not args.custom_script and args.mode == "connect" and args.timeout < 3.0:
        print(f"[THÔNG BÁO] Chế độ connect tự động nâng timeout từ {args.timeout}s lên tối thiểu 3.0s (Windows loopback safety).")
        args.timeout = 3.0

    # Safety Barrier: send / both requires --yes
    if args.mode in ("send", "both") and not args.yes:
        print("================================================================================", file=sys.stderr)
        print(f"[CHẶN AN TOÀN] Bạn đang yêu cầu chạy ở chế độ '--mode {args.mode}'.", file=sys.stderr)
        print("Chế độ này sẽ gửi dữ liệu telemetry mẫu vào mạng hoặc platform đích.", file=sys.stderr)
        print("Để tiếp tục, bạn bắt buộc phải truyền cờ '--yes' để xác nhận thực thi:", file=sys.stderr)
        cmd_args = " ".join(sys.argv[1:]) if sys.argv[1:] else f"--mode {args.mode}"
        print(f"  python run_sims.py {cmd_args} --yes", file=sys.stderr)
        print("================================================================================", file=sys.stderr)
        sys.exit(10)

    # Build or Filter Registry
    custom_args_map = parse_custom_args(args.custom_args)

    if args.custom_script:
        # Custom stub execution for step e verification
        registry = [
            CLIEntry(
                name="stub",
                script=args.custom_script,
                group="custom",
                grace=args.grace if args.grace is not None else 1.0,
            )
        ]
    else:
        all_entries = get_default_registry()

        # Parse --only and --skip
        only_set = {x.strip().lower() for x in args.only.split(",") if x.strip()}
        if "tcp_udp" in only_set:
            only_set.add("tcp")
            only_set.add("udp")

        skip_set = {x.strip().lower() for x in args.skip.split(",") if x.strip()}
        if "tcp_udp" in skip_set:
            skip_set.add("tcp")
            skip_set.add("udp")

        registry = [
            e for e in all_entries
            if (not only_set or e.name.lower() in only_set)
            and (e.name.lower() not in skip_set)
        ]

    if not registry:
        print("[LỖI] Không có simulator nào khớp với bộ lọc (--only / --skip).", file=sys.stderr)
        sys.exit(10)

    # Dry-Run Mode
    if args.dry_run:
        print("\n=== [DRY-RUN] DANH SÁCH LỆNH CHẠY CỦA CÁC SIMULATOR ===")
        print(f"Chế độ: {args.mode} | Host: {target_host} | Timeout: {args.timeout}s\n")
        for entry in registry:
            # Check TFTP safety in dry-run
            if entry.name == "tftp" and args.mode in ("send", "both") and not args.allow_write:
                print(f"[DRY-RUN] {entry.name:<10} (nhóm: {entry.group:<8}): SKIPPED (needs --allow-write)")
                continue

            cmd, notes = build_cli_command(entry, args, config, cwd=".", custom_args_map=custom_args_map)
            notes_str = f" [{'; '.join(notes)}]" if notes else ""
            print(f"[DRY-RUN] {entry.name:<10} (nhóm: {entry.group:<8}): {' '.join(cmd)}{notes_str}")
            if args.show_payload:
                print_payload_info(entry.name, config, target_host, args.mode)
        print("\n" + MANDATORY_NOTICE + "\n")
        sys.exit(0)

    # Prepare Run Output Directory
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = args.out_dir if args.out_dir else os.path.join("logs", "runs", timestamp)
    os.makedirs(run_dir, exist_ok=True)

    # Java Process Snapshot BEFORE execution
    java_pids_before = get_java_pids()

    results: List[ParsedResult] = []

    print(f"\n[*] Bắt đầu thực thi {len(registry)} simulator(s) tuần tự...")
    print(f"[*] Thư mục lưu kết quả: {run_dir}\n")

    for entry in registry:
        # Check TFTP safety
        if entry.name == "tftp" and args.mode in ("send", "both") and not args.allow_write:
            print(f">>> Bỏ qua tftp: Thiếu cờ --allow-write ở chế độ {args.mode}.")
            results.append(
                ParsedResult(
                    proto="tftp",
                    mode=args.mode,
                    status="SKIPPED",
                    exit_code=-1,
                    elapsed=0.0,
                    detail="SKIPPED: Cần cờ --allow-write để thực hiện TFTP WRQ",
                    notes=["SKIPPED (needs --allow-write)"],
                    raw_cmd="(skipped)",
                    log_file="",
                )
            )
            continue

        cmd, notes = build_cli_command(entry, args, config, cwd=".", custom_args_map=custom_args_map)
        log_file = os.path.join(run_dir, f"{entry.name}.txt")

        # Determine grace
        grace_to_use = args.grace if args.grace is not None else entry.grace

        # Determine target port for advisory
        proto_port = entry.default_port
        if entry.name in adapters_cfg and isinstance(adapters_cfg[entry.name], dict) and "port" in adapters_cfg[entry.name]:
            proto_port = adapters_cfg[entry.name]["port"]

        print(f"[{entry.name.upper()}] Đang chạy: {' '.join(cmd)}")
        if args.show_payload:
            print_payload_info(entry.name, config, target_host, args.mode)
        exec_res = execute_cli(
            cmd=cmd,
            timeout=args.timeout,
            grace=grace_to_use,
            cwd=".",
            out_file=log_file,
        )

        parsed = parse_cli_output(
            proto_name=entry.name,
            mode=args.mode,
            exec_res=exec_res,
            target_port=proto_port,
            target_host=target_host,
            initial_notes=notes,
            log_file=log_file,
        )
        results.append(parsed)

    # Java Process Snapshot AFTER execution
    import time
    time.sleep(1.0)
    java_pids_after = get_java_pids()
    new_java_pids = java_pids_after - java_pids_before

    # Print Console Summary Table
    print_console_table(results)

    # Write Results to JSON and MD
    json_path = os.path.join(run_dir, "results.json")
    md_path = os.path.join(run_dir, "results.md")
    meta = {
        "timestamp": timestamp,
        "mode": args.mode,
        "target_host": target_host,
        "timeout": args.timeout,
        "run_dir": run_dir,
        "show_payload": args.show_payload,
    }
    write_results_json(results, json_path, meta, config=config)
    write_results_md(results, md_path, meta, config=config)
    print(f"[*] Báo cáo đã lưu: {json_path}")
    print(f"[*] Báo cáo đã lưu: {md_path}")

    # Check for leaked Java processes
    if new_java_pids:
        print("================================================================================", file=sys.stderr)
        print(f"[CẢNH BÁO TIẾN TRÌNH] Phát hiện tiến trình Java mới chưa được dọn dẹp sau khi chạy: PID {sorted(list(new_java_pids))}.", file=sys.stderr)
        print("Runner KHÔNG tự kill các tiến trình này. Vui lòng kiểm tra hoặc dọn dẹp bằng scripts/teardown.ps1.", file=sys.stderr)
        print("================================================================================", file=sys.stderr)
        sys.exit(11)

    sys.exit(0)


if __name__ == "__main__":
    main()
