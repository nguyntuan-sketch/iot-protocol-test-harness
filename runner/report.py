"""
Runner Report Module
Generates console summary table, JSON export, and Markdown report.
"""

import json
import os
from collections import Counter
from typing import Any, Dict, List, Optional
from runner.parse import ParsedResult

MANDATORY_NOTICE = """====================================================================================================
GHI CHÚ QUAN TRỌNG:
- TIMEOUT ở UDP/DDS nghĩa là chưa biết, không phải hỏng.
- OK ở TCP chỉ là bắt tay, không chứng minh dữ liệu đã được xử lý.
===================================================================================================="""


def format_detail_column(res: ParsedResult, max_len: int = 90) -> str:
    """Combines detail text and notes, truncating to max_len."""
    parts = []
    if res.detail:
        parts.append(res.detail)
    if res.notes:
        parts.append(f"[{'; '.join(res.notes)}]")
    full_text = " | ".join(parts) if parts else "-"
    if len(full_text) > max_len:
        return full_text[: max_len - 3] + "..."
    return full_text


def print_console_table(results: List[ParsedResult]) -> None:
    """
    Renders the aligned console table for simulator runs.
    """
    col_w = {
        "proto": 10,
        "mode": 8,
        "status": 15,
        "exit": 6,
        "time": 8,
        "detail": 90,
    }

    header = (
        f"| {'PROTO':<{col_w['proto']}} "
        f"| {'MODE':<{col_w['mode']}} "
        f"| {'RESULT':<{col_w['status']}} "
        f"| {'EXIT':<{col_w['exit']}} "
        f"| {'TIME':<{col_w['time']}} "
        f"| {'DETAIL':<{col_w['detail']}} |"
    )
    separator = (
        f"|{'-' * (col_w['proto'] + 2)}"
        f"|{'-' * (col_w['mode'] + 2)}"
        f"|{'-' * (col_w['status'] + 2)}"
        f"|{'-' * (col_w['exit'] + 2)}"
        f"|{'-' * (col_w['time'] + 2)}"
        f"|{'-' * (col_w['detail'] + 2)}|"
    )

    print("\n" + separator)
    print(header)
    print(separator)

    for r in results:
        detail_str = format_detail_column(r, col_w["detail"])
        time_str = f"{r.elapsed:.2f}s"
        exit_str = str(r.exit_code) if r.exit_code != -1 else "-"
        row = (
            f"| {r.proto:<{col_w['proto']}} "
            f"| {r.mode:<{col_w['mode']}} "
            f"| {r.status:<{col_w['status']}} "
            f"| {exit_str:<{col_w['exit']}} "
            f"| {time_str:<{col_w['time']}} "
            f"| {detail_str:<{col_w['detail']}} |"
        )
        print(row)

    print(separator)

    # Summary Counts
    counts = Counter(r.status for r in results)
    total = len(results)
    summary_parts = [f"Total: {total}"]
    status_order = [
        "OK",
        "REFUSED",
        "TIMEOUT",
        "AUTH_FAILED",
        "PROTO_ERROR",
        "NO_RESULT_LINE",
        "RUNNER_TIMEOUT",
        "SKIPPED",
    ]
    for s in status_order:
        if s in counts or s in ("OK", "REFUSED", "TIMEOUT", "AUTH_FAILED", "PROTO_ERROR"):
            summary_parts.append(f"{s}: {counts[s]}")

    print(" | ".join(summary_parts))
    print("\n" + MANDATORY_NOTICE + "\n")


def write_results_json(
    results: List[ParsedResult],
    filepath: str,
    meta: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
) -> None:
    """Saves structured execution results as JSON."""
    from runner.registry import get_protocol_payload_info

    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    cfg = config or {}
    show_payload = meta.get("show_payload", False)

    data = {
        "metadata": meta,
        "results": [
            {
                "proto": r.proto,
                "mode": r.mode,
                "status": r.status,
                "exit_code": r.exit_code,
                "elapsed": r.elapsed,
                "detail": r.detail,
                "notes": r.notes,
                "raw_cmd": r.raw_cmd,
                "log_file": r.log_file,
                "payload_info": (
                    get_protocol_payload_info(r.proto, cfg, meta.get("target_host", "127.0.0.1"), r.mode)
                    if show_payload
                    else None
                ),
            }
            for r in results
        ],
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def write_results_md(
    results: List[ParsedResult],
    filepath: str,
    meta: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
) -> None:
    """Saves summary report as GitHub-flavored Markdown."""
    from runner.registry import get_protocol_payload_info

    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    counts = Counter(r.status for r in results)
    total = len(results)

    lines = [
        "# Báo Cáo Chạy Gộp Simulators (`run_sims.py`)\n",
        f"- **Thời gian chạy:** {meta.get('timestamp', '-')}",
        f"- **Chế độ:** `{meta.get('mode', '-')}`",
        f"- **Mục tiêu (Host):** `{meta.get('target_host', '-')}`",
        f"- **Timeout cấu hình:** `{meta.get('timeout', '-')}s`",
        f"- **Thư mục log chi tiết:** `{meta.get('run_dir', '-')}`\n",
        "## 1. Bảng Kết Quả Thực Thi\n",
        "| Proto | Mode | Result | Exit | Time | Detail & Notes |",
        "|---|---|---|---|---|---|",
    ]

    for r in results:
        detail_esc = format_detail_column(r, 120).replace("|", "\\|")
        exit_str = str(r.exit_code) if r.exit_code != -1 else "-"
        lines.append(
            f"| `{r.proto}` | `{r.mode}` | **{r.status}** | `{exit_str}` | `{r.elapsed:.2f}s` | {detail_esc} |"
        )

    lines.append("\n## 2. Tổng Hợp Trạng Thái\n")
    summary_parts = [f"**Total:** {total}"]
    for s in ["OK", "REFUSED", "TIMEOUT", "AUTH_FAILED", "PROTO_ERROR", "NO_RESULT_LINE", "RUNNER_TIMEOUT", "SKIPPED"]:
        summary_parts.append(f"**{s}:** {counts[s]}")
    lines.append(" | ".join(summary_parts) + "\n")

    lines.append("## 3. Ghi Chú Cố Định\n")
    lines.append("> [!NOTE]")
    lines.append("> - **TIMEOUT ở UDP/DDS nghĩa là chưa biết, không phải hỏng.**")
    lines.append("> - **OK ở TCP chỉ là bắt tay, không chứng minh dữ liệu đã được xử lý.**\n")

    if meta.get("show_payload"):
        cfg = config or {}
        lines.append("## 4. Chi Tiết Cấu Trúc Bản Tin Đã Gửi\n")
        for r in results:
            info = get_protocol_payload_info(r.proto, cfg, meta.get("target_host", "127.0.0.1"), r.mode)
            lines.append(f"### {r.proto.upper()} ({info['transport']})")
            lines.append(f"- **Đích đến:** `{info['target']}`")
            if info.get("headers"):
                hdr_str = ", ".join(f"`{k}: {v}`" for k, v in info["headers"].items())
                lines.append(f"- **Headers/Meta:** {hdr_str}")
            lines.append(f"- **Định dạng:** {info['format']}")
            lines.append(f"- **Mô tả:** {info['description']}")
            if info.get("payload"):
                lines.append("```json")
                lines.append(json.dumps(info["payload"], indent=2, ensure_ascii=False))
                lines.append("```\n")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

