"""
Runner Result Parsing Module
Parses RESULT lines, validates exit codes, and extracts protocol status and diagnostics.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from runner.execute import ExecutionResult

EXPECTED_EXIT_MAP = {
    "OK": 0,
    "PROTO_ERROR": 1,
    "REFUSED": 2,
    "TIMEOUT": 3,
    "AUTH_FAILED": 4,
}

VALID_STATUSES = {"OK", "REFUSED", "TIMEOUT", "AUTH_FAILED", "PROTO_ERROR"}


@dataclass
class ParsedResult:
    proto: str
    mode: str
    status: str
    exit_code: int
    elapsed: float
    detail: str
    notes: List[str] = field(default_factory=list)
    raw_cmd: str = ""
    log_file: str = ""


def parse_cli_output(
    proto_name: str,
    mode: str,
    exec_res: ExecutionResult,
    target_port: Optional[int] = None,
    target_host: Optional[str] = None,
    initial_notes: Optional[List[str]] = None,
    log_file: str = "",
) -> ParsedResult:
    """
    Parses the raw output from a CLI execution into a structured ParsedResult.
    """
    notes = list(initial_notes or [])

    if exec_res.timed_out:
        return ParsedResult(
            proto=proto_name,
            mode=mode,
            status="RUNNER_TIMEOUT",
            exit_code=-1,
            elapsed=exec_res.elapsed,
            detail=f"Quá hạn thời gian (timeout cứng bị ngắt bởi runner)",
            notes=notes,
            raw_cmd=exec_res.cmd_str,
            log_file=log_file,
        )

    # Search for the LAST RESULT: line in stdout, fallback to stderr
    combined_lines = exec_res.stdout.splitlines()
    result_line: Optional[str] = None
    for line in reversed(combined_lines):
        if line.strip().startswith("RESULT:"):
            result_line = line.strip()
            break

    if not result_line:
        for line in reversed(exec_res.stderr.splitlines()):
            if line.strip().startswith("RESULT:"):
                result_line = line.strip()
                break

    if not result_line:
        # No RESULT line found (e.g. fatal error, crash, unrecognized argument)
        err_lines = [l.strip() for l in exec_res.stderr.splitlines() if l.strip()]
        if not err_lines:
            err_lines = [l.strip() for l in exec_res.stdout.splitlines() if l.strip()]
        last_err = " | ".join(err_lines[-20:]) if err_lines else "No output produced"

        return ParsedResult(
            proto=proto_name,
            mode=mode,
            status="NO_RESULT_LINE",
            exit_code=exec_res.returncode,
            elapsed=exec_res.elapsed,
            detail=last_err[:200],
            notes=notes,
            raw_cmd=exec_res.cmd_str,
            log_file=log_file,
        )

    # Parse RESULT: <proto> [mode=STATUS | STATUS] detail=<detail>
    status = "UNKNOWN"
    detail = ""

    # Match detail part
    detail_match = re.search(r"detail=(.*)$", result_line)
    if detail_match:
        detail = detail_match.group(1).strip()
        prefix = result_line[:detail_match.start()].strip()
    else:
        prefix = result_line

    # Find status in prefix
    status_match = re.search(r"(?:connect|send|listen|both|status)=(\w+)", prefix, re.IGNORECASE)
    if status_match:
        cand = status_match.group(1).upper()
        if cand in VALID_STATUSES:
            status = cand
    if status == "UNKNOWN":
        for tok in prefix.split():
            tok_upper = tok.upper()
            if tok_upper in VALID_STATUSES:
                status = tok_upper
                break

    if status == "UNKNOWN":
        status = "PROTO_ERROR"

    # Check exit code consistency
    if status in EXPECTED_EXIT_MAP:
        exp_code = EXPECTED_EXIT_MAP[status]
        if exec_res.returncode != exp_code:
            notes.append(f"exit mismatch (code {exec_res.returncode})")

    # Check listen / downlink empty indicator
    if mode in ("listen", "both"):
        if "Recv:0" in detail or "no downlink received" in detail.lower():
            notes.append("(no downlink)")

    # Check port 8080 advisory for HTTP/WebSocket
    if target_port == 8080 and proto_name in ("http", "websocket"):
        server_match = re.search(r"Server:\s*([^\|\,\;]+)", detail, re.IGNORECASE)
        if server_match:
            srv = server_match.group(1).strip()
            notes.append(f"Server: {srv} (cổng 8080 có thể do dịch vụ khác chiếm)")
        else:
            notes.append("cổng 8080 có thể do dịch vụ khác chiếm")

    # Check localhost advisory
    if target_host and target_host.lower() == "localhost":
        notes.append("khuyên dùng 127.0.0.1")

    return ParsedResult(
        proto=proto_name,
        mode=mode,
        status=status,
        exit_code=exec_res.returncode,
        elapsed=exec_res.elapsed,
        detail=detail,
        notes=notes,
        raw_cmd=exec_res.cmd_str,
        log_file=log_file,
    )
