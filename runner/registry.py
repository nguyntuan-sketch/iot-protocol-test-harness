"""
Runner Registry Module
Manages CLI definitions, capability discovery, and command-line construction.
"""

import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

CHECK_FLAGS = [
    "--host",
    "--port",
    "--timeout",
    "--count",
    "--interval",
    "--duration",
    "--server-uri",
    "--domain-id",
    "--transport",
    "--mode",
]

_FLAGS_CACHE: Dict[str, Set[str]] = {}


@dataclass
class CLIEntry:
    name: str
    script: str
    group: str
    grace: float
    extra_args: List[str] = field(default_factory=list)
    default_port: Optional[int] = None
    supported_flags: Set[str] = field(default_factory=set)


def get_default_registry() -> List[CLIEntry]:
    """
    Returns the standard 12-item registry for all 11 protocol simulators
    (with tcp_udp split into tcp and udp).
    """
    return [
        CLIEntry(
            name="http",
            script="sim/http_sim.py",
            group="port8080",
            grace=5.0,
            default_port=8080,
        ),
        CLIEntry(
            name="mqtt",
            script="sim/mqtt_sim.py",
            group="port1883",
            grace=5.0,
            default_port=1883,
        ),
        CLIEntry(
            name="coap",
            script="sim/coap_sim.py",
            group="udp5683",
            grace=5.0,
            default_port=5683,
        ),
        CLIEntry(
            name="xmpp",
            script="sim/xmpp_sim.py",
            group="port5222",
            grace=5.0,
            default_port=5222,
        ),
        CLIEntry(
            name="amqp",
            script="sim/amqp_sim.py",
            group="port5672",
            grace=5.0,
            default_port=5672,
        ),
        CLIEntry(
            name="dds",
            script="sim/dds_sim.py",
            group="multicast",
            grace=5.0,
            default_port=None,
        ),
        CLIEntry(
            name="lwm2m",
            script="sim/lwm2m_sim.py",
            group="udp5683",
            grace=15.0,  # lwm2m >= 15s grace for Leshan Java client lifecycle
            default_port=5683,
        ),
        CLIEntry(
            name="lorawan",
            script="sim/lorawan_sim.py",
            group="port1700",
            grace=5.0,
            default_port=1700,
        ),
        CLIEntry(
            name="websocket",
            script="sim/websocket_sim.py",
            group="port8080",
            grace=5.0,
            default_port=8080,
        ),
        CLIEntry(
            name="tcp",
            script="sim/tcp_udp_sim.py",
            group="port9000",
            grace=5.0,
            extra_args=["--transport", "tcp"],
            default_port=9000,
        ),
        CLIEntry(
            name="udp",
            script="sim/tcp_udp_sim.py",
            group="port9001",
            grace=5.0,
            extra_args=["--transport", "udp"],
            default_port=9001,
        ),
        CLIEntry(
            name="tftp",
            script="sim/tftp_sim.py",
            group="port69",
            grace=5.0,
            default_port=69,
        ),
    ]


def detect_supported_flags(python_exe: str, script_path: str, cwd: str = ".") -> Set[str]:
    """
    Runs `<cli> --help` once and caches the set of recognized flags.
    """
    abs_script = os.path.abspath(os.path.join(cwd, script_path))
    if abs_script in _FLAGS_CACHE:
        return _FLAGS_CACHE[abs_script]

    supported = set()
    if not os.path.exists(abs_script):
        _FLAGS_CACHE[abs_script] = supported
        return supported

    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            [python_exe, abs_script, "--help"],
            capture_output=True,
            text=True,
            timeout=8,
            cwd=cwd,
            env=env,
            encoding="utf-8",
            errors="replace",
        )
        help_text = (proc.stdout or "") + " " + (proc.stderr or "")
        found_flags = set(re.findall(r"(--[a-zA-Z0-9-]+)", help_text))
        for flag in CHECK_FLAGS:
            if flag in found_flags:
                supported.add(flag)
    except Exception:
        pass

    _FLAGS_CACHE[abs_script] = supported
    return supported


def parse_custom_args(arg_list: Optional[List[str]]) -> Dict[str, List[str]]:
    """
    Parses repeated --arg PROTO="<raw>" into {proto: [tokens]} using shlex.split.
    """
    parsed: Dict[str, List[str]] = {}
    if not arg_list:
        return parsed

    for item in arg_list:
        if "=" not in item:
            continue
        proto_key, raw_args = item.split("=", 1)
        proto_key = proto_key.strip().lower()
        try:
            tokens = shlex.split(raw_args.strip("\"' "))
        except Exception:
            tokens = raw_args.strip("\"' ").split()
        if proto_key not in parsed:
            parsed[proto_key] = []
        parsed[proto_key].extend(tokens)

    return parsed


def build_cli_command(
    entry: CLIEntry,
    args,
    config: dict,
    cwd: str = ".",
    custom_args_map: Optional[Dict[str, List[str]]] = None,
) -> Tuple[List[str], List[str]]:
    """
    Constructs the exact command list to invoke a CLI simulator,
    ensuring only supported flags are passed, and returning any notes.
    """
    python_exe = sys.executable
    supported = detect_supported_flags(python_exe, entry.script, cwd=cwd)
    entry.supported_flags = supported

    cmd: List[str] = [python_exe, entry.script]
    notes: List[str] = []

    # 1. Mode
    if "--mode" in supported:
        cmd.extend(["--mode", args.mode])

    # 2. Host & Port Resolution
    target_host = args.host if getattr(args, "host", None) else config.get("global", {}).get("target_host", "127.0.0.1")
    
    # Resolve Port from config.yaml adapters section or entry default
    adapters_cfg = config.get("adapters", {})
    proto_port = entry.default_port
    if entry.name in adapters_cfg and isinstance(adapters_cfg[entry.name], dict) and "port" in adapters_cfg[entry.name]:
        proto_port = adapters_cfg[entry.name]["port"]
    elif entry.name == "tcp" and "tcp_udp" in adapters_cfg and "tcp_port" in adapters_cfg["tcp_udp"]:
        proto_port = adapters_cfg["tcp_udp"]["tcp_port"]
    elif entry.name == "udp" and "tcp_udp" in adapters_cfg and "udp_port" in adapters_cfg["tcp_udp"]:
        proto_port = adapters_cfg["tcp_udp"]["udp_port"]

    if entry.name == "lwm2m":
        if "--server-uri" in supported:
            port_to_use = proto_port or 5683
            server_uri = f"coap://{target_host}:{port_to_use}"
            cmd.extend(["--server-uri", server_uri])
    else:
        if "--host" in supported:
            cmd.extend(["--host", target_host])
        else:
            if target_host != "127.0.0.1" or getattr(args, "host", None) or entry.name == "dds":
                notes.append("host ignored")

        if "--port" in supported and proto_port is not None:
            cmd.extend(["--port", str(proto_port)])

    # 3. Timeout
    if "--timeout" in supported:
        timeout_val = getattr(args, "timeout", 5.0)
        if args.mode == "connect" and timeout_val < 3.0:
            timeout_val = 3.0
        cmd.extend(["--timeout", str(timeout_val)])

    # 4. Count, Interval, Duration
    if getattr(args, "count", None) is not None and "--count" in supported:
        cmd.extend(["--count", str(args.count)])
    if getattr(args, "interval", None) is not None and "--interval" in supported:
        cmd.extend(["--interval", str(args.interval)])
    if getattr(args, "duration", None) is not None and "--duration" in supported:
        cmd.extend(["--duration", str(args.duration)])

    # 5. Extra predefined args (e.g. --transport tcp)
    if entry.extra_args:
        cmd.extend(entry.extra_args)

    # 6. User raw custom args via --arg PROTO="<raw>"
    if custom_args_map and entry.name.lower() in custom_args_map:
        cmd.extend(custom_args_map[entry.name.lower()])

    return cmd, notes
