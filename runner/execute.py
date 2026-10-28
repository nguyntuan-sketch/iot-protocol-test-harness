"""
Runner Execution Module
Handles environment validation, Java process tracking, and subprocess execution
with Windows Job Object tree cleanup to guarantee no orphan background processes.
"""

import ctypes
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Set

if sys.platform == "win32":
    from ctypes import wintypes

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryLimit", ctypes.c_size_t),
            ("PeakJobMemoryLimit", ctypes.c_size_t),
        ]

    def _create_kill_on_close_job():
        """Creates a Windows Job Object that terminates all child processes when closed."""
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            hJob = kernel32.CreateJobObjectW(None, None)
            if not hJob:
                return None
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            JobObjectExtendedLimitInformation = 9
            kernel32.SetInformationJobObject(
                hJob,
                JobObjectExtendedLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            return hJob
        except Exception:
            return None
else:
    def _create_kill_on_close_job():
        return None


@dataclass
class ExecutionResult:
    returncode: int
    stdout: str
    stderr: str
    elapsed: float
    timed_out: bool
    cmd_str: str


def check_environment() -> None:
    """
    Validates required dependencies (pika, cyclonedds, yaml).
    Exits with code 10 if any dependency is missing.
    """
    missing = []
    for mod in ["pika", "cyclonedds", "yaml"]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)

    if missing:
        print("================================================================================", file=sys.stderr)
        print(f"[LỖI MÔI TRƯỜNG] Không thể import các module bắt buộc: {', '.join(missing)}.", file=sys.stderr)
        print(f"Môi trường Python hiện tại: {sys.executable}", file=sys.stderr)
        print("Vui lòng kích hoạt Conda environment 'iot-test' (conda activate iot-test) hoặc truyền đường dẫn Python hợp lệ:", file=sys.stderr)
        print("  python run_sims.py", file=sys.stderr)
        print("================================================================================", file=sys.stderr)
        sys.exit(10)


def get_java_pids() -> Set[int]:
    """
    Captures currently running java.exe process IDs via tasklist.
    """
    pids: Set[int] = set()
    try:
        proc = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq java.exe"],
            capture_output=True,
            text=True,
            timeout=6,
            encoding="utf-8",
            errors="replace",
        )
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line or "No tasks are running" in line or "INFO:" in line:
                continue
            parts = [p.strip('"\r ') for p in line.split(",")]
            if len(parts) >= 2 and parts[0].lower() == "java.exe":
                try:
                    pids.add(int(parts[1]))
                except ValueError:
                    pass
    except Exception:
        pass
    return pids


def execute_cli(
    cmd: List[str],
    timeout: float,
    grace: float,
    cwd: str,
    out_file: str,
    redactor: Optional[Any] = None,
) -> ExecutionResult:
    """
    Executes a single CLI simulator using subprocess.Popen.
    Attaches the process to a Windows Job Object with KILL_ON_CLOSE to guarantee
    all descendant processes (like Java JVM launched by LwM2M wrapper) are cleanly reaped.
    Enforces a hard timeout (timeout + grace).
    Saves raw stdout/stderr to out_file (redacting credentials before writing).
    """
    abs_out_file = os.path.abspath(os.path.join(cwd, out_file))
    os.makedirs(os.path.dirname(abs_out_file), exist_ok=True)

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    t0 = time.time()
    timed_out = False
    stdout = ""
    stderr = ""
    returncode = 0

    hJob = _create_kill_on_close_job()

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        env=env,
    )

    if hJob and sys.platform == "win32":
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.AssignProcessToJobObject(hJob, proc._handle)
        except Exception:
            pass

    total_timeout = timeout + grace
    try:
        stdout, stderr = proc.communicate(timeout=total_timeout)
        returncode = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        returncode = -1
        # Forcefully terminate tree
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

        try:
            rem_out, rem_err = proc.communicate(timeout=2)
            stdout = (stdout or "") + (rem_out or "")
            stderr = (stderr or "") + (rem_err or "")
        except Exception:
            pass
    finally:
        if hJob and sys.platform == "win32":
            try:
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.CloseHandle(hJob)
            except Exception:
                pass

    elapsed = round(time.time() - t0, 3)

    raw_cmd_str = " ".join(cmd)
    logged_cmd = redactor.redact_text(raw_cmd_str) if redactor else raw_cmd_str
    logged_stdout = redactor.redact_text(stdout or "") if redactor else (stdout or "")
    logged_stderr = redactor.redact_text(stderr or "") if redactor else (stderr or "")

    # Save raw log output (redacted)
    try:
        with open(abs_out_file, "w", encoding="utf-8") as f:
            f.write(f"COMMAND: {logged_cmd}\n")
            f.write(f"ELAPSED: {elapsed}s\n")
            f.write(f"TIMED_OUT: {timed_out}\n")
            f.write(f"RETURNCODE: {returncode}\n\n")
            f.write("=== STDOUT ===\n")
            f.write(logged_stdout)
            f.write("\n\n=== STDERR ===\n")
            f.write(logged_stderr)
    except Exception:
        pass

    return ExecutionResult(
        returncode=returncode,
        stdout=stdout or "",
        stderr=stderr or "",
        elapsed=elapsed,
        timed_out=timed_out,
        cmd_str=logged_cmd,
    )
