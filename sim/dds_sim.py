#!/usr/bin/env python3
"""
DDS IoT Protocol Simulator CLI
Protocol #6: DDS (Eclipse CycloneDDS 11.0.1 native)
Usage:
  python sim/dds_sim.py --mode connect --timeout 3
  python sim/dds_sim.py --mode both --count 3 --duration 5
  python sim/dds_sim.py --mode send --count 5
  python sim/dds_sim.py --mode listen --duration 8
"""

import os
import sys
import time
import logging
import argparse
from datetime import datetime
from dataclasses import dataclass

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
    format_result
, setup_simulator_logger)

try:
    import yaml
except ImportError:
    yaml = None

try:
    import cyclonedds
    from cyclonedds.domain import DomainParticipant
    from cyclonedds.topic import Topic
    from cyclonedds.pub import DataWriter
    from cyclonedds.sub import DataReader
    from cyclonedds.builtin import BuiltinDataReader, BuiltinTopicDcpsParticipant
    from cyclonedds.idl import IdlStruct
    from cyclonedds.idl.types import int64
except ImportError as e:
    print(f"[ERROR] cyclonedds native library not available: {e}", file=sys.stderr)
    print("        Please run inside conda environment 'iot-test' (Python 3.12).", file=sys.stderr)
    sys.exit(1)


# IDL Data Model with int64 timestamp
@dataclass
class Telemetry(IdlStruct):
    device_id: str
    timestamp: int64
    temperature: float
    humidity: float
    status: str


def get_default_config():
    cfg_path = os.path.join(base_dir, "config", "config.yaml")
    cfg = {
        "domain_id": 0,
        "topic_name": "IoTTelemetryTopic"
    }
    if yaml and os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
                dds = raw.get("adapters", {}).get("dds", {})
                cfg["domain_id"] = dds.get("domain_id", cfg["domain_id"])
                cfg["topic_name"] = dds.get("topic_name", cfg["topic_name"])
        except Exception as e:
            sys.stderr.write(f"[WARN] Failed to read config.yaml: {e}\n")
    return cfg


def setup_logger(args=None):
    return setup_simulator_logger("dds", args=args)

def main():
    cfg = get_default_config()
    parser = argparse.ArgumentParser(
        description="DDS IoT Protocol Simulator CLI (CycloneDDS native)",
        epilog="Chế độ 'connect' phát hiện participant từ xa qua BuiltinTopicDcpsParticipant. Không hỗ trợ TLS (dùng DDS-SEC)."
    )
    parser.add_argument("--mode", choices=["connect", "send", "listen", "both"], default="connect",
                        help="Operating mode: 'connect', 'send', 'listen', or 'both' (default: connect)")
    parser.add_argument("--domain-id", type=int, default=cfg["domain_id"], help=f"DDS Domain ID (default: {cfg['domain_id']})")
    parser.add_argument("--topic", default=cfg["topic_name"], help=f"DDS Topic name (default: {cfg['topic_name']})")
    parser.add_argument("--device-id", default="sim_dds_001", help="Device ID")
    parser.add_argument("--tls", action="store_true", help="Request TLS (Not supported in DDS; uses DDS-Security)")
    parser.add_argument("--insecure", action="store_true", help="Unused for DDS")
    parser.add_argument("--count", type=int, default=1, help="Samples count to write (default: 1)")
    parser.add_argument("--interval", type=float, default=1.0, help="Interval between writes in seconds (default: 1.0)")
    parser.add_argument("--duration", type=float, default=5.0, help="Listening duration in seconds (default: 5.0)")
    parser.add_argument("--timeout", type=float, default=3.0, help="Discovery/Match timeout in seconds (default: 3.0)")

    args = parser.parse_args()
    logger = setup_logger(args)

    if args.tls:
        logger.warning("[!] TLS not supported in this simulator (DDS uses DDS Security / OMG DDS-SEC plugin instead of TLS)")

    logger.info(f"[*] Starting DDS Simulator in '{args.mode}' mode...")
    dds_ver = getattr(cyclonedds, '__version__', '11.0.1')
    logger.info(f"    CycloneDDS Version: {dds_ver} | Domain ID: {args.domain_id} | Topic: {args.topic}")

    try:
        participant = DomainParticipant(args.domain_id)
        topic = Topic(participant, args.topic, Telemetry)
        logger.info(f"[{datetime.now().isoformat()}] <--- [DDS INITIALIZED] Local DomainParticipant (GUID: {participant.guid}) & Topic '{args.topic}' created.")
    except Exception as e:
        logger.error(f"[!] Cannot initialize DDS DomainParticipant (domain={args.domain_id}): {e}")
        return format_result("dds", args.mode, STATUS_PROTO_ERROR, f"Participant initialization failed: {e}", stage="init")

    # Mode: CONNECT only
    # Requirement: Không có DataReader cục bộ. Chỉ báo OK khi thấy participant khác chính mình.
    if args.mode == "connect":
        my_guid = str(participant.guid)
        logger.info(f"[*] Local Participant GUID: {my_guid}")
        logger.info(f"[*] Discovering remote participants on Domain {args.domain_id} for {args.timeout}s...")

        try:
            builtin_reader = BuiltinDataReader(participant, BuiltinTopicDcpsParticipant)
        except Exception as be:
            logger.error(f"[!] Cannot create BuiltinDataReader: {be}")
            return format_result("dds", "connect", STATUS_PROTO_ERROR, f"BuiltinDataReader error: {be}", stage="discovery")

        t_start = time.time()
        discovered_remotes = set()

        while time.time() - t_start < args.timeout:
            samples = builtin_reader.take()
            for s in samples:
                guid_str = str(getattr(s, 'key', s))
                if guid_str and guid_str != my_guid:
                    discovered_remotes.add(guid_str)
            if discovered_remotes:
                break
            time.sleep(0.2)

        if discovered_remotes:
            remotes_str = ", ".join(sorted(discovered_remotes))
            logger.info(f"[*] Discovered {len(discovered_remotes)} remote participant(s): {remotes_str}")
            return format_result("dds", "connect", STATUS_OK,
                                 f"Discovered {len(discovered_remotes)} remote participant(s) (GUIDs: {remotes_str})",
                                 stage="discovery")
        else:
            logger.error(f"[!] No remote participant found in domain {args.domain_id} after {args.timeout}s.")
            return format_result("dds", "connect", STATUS_TIMEOUT,
                                 f"No remote participant found in domain {args.domain_id} after {args.timeout}s (multicast issue likely; configure peers via CYCLONEDDS_URI)",
                                 stage="discovery")

    reader = None
    if args.mode in ["listen", "both"]:
        try:
            reader = DataReader(participant, topic)
            logger.info(f"[*] DDS DataReader attached to topic '{args.topic}'.")
        except Exception as e:
            logger.error(f"[!] Cannot create DDS DataReader: {e}")
            return format_result("dds", args.mode, STATUS_PROTO_ERROR, f"DataReader error: {e}", stage="init")

    # SEND or BOTH
    if args.mode in ["send", "both"]:
        try:
            writer = DataWriter(participant, topic)
            logger.info(f"[*] Publishing {args.count} sample(s) via DDS DataWriter on topic '{args.topic}'...")

            # Check publication matched status (requirement: write() without error is NOT proof)
            logger.info(f"[*] Checking publication matched status (timeout {args.timeout}s)...")
            t_match_start = time.time()
            matched_count = 0
            while time.time() - t_match_start < args.timeout:
                status = writer.get_publication_matched_status()
                matched_count = getattr(status, "current_count", 0)
                if matched_count > 0:
                    break
                time.sleep(0.2)

            logger.info(f"[*] Matched remote DataReader count: {matched_count}")

            for i in range(1, args.count + 1):
                sample = Telemetry(
                    device_id=args.device_id,
                    timestamp=int64(int(time.time() * 1000)),
                    temperature=float(25.0 + i * 0.5),
                    humidity=float(50.0 - i * 0.2),
                    status="NORMAL"
                )
                writer.write(sample)
                logger.info(f"[{datetime.now().isoformat()}] ---> DDS WRITE: device_id='{sample.device_id}', temp={sample.temperature}, hum={sample.humidity}, ts={sample.timestamp}")
                if i < args.count:
                    time.sleep(args.interval)

            if matched_count == 0:
                logger.warning(f"[!] Write completed locally but publication_matched_status.current_count is 0 (no remote reader on topic '{args.topic}')")
                return format_result("dds", args.mode, STATUS_TIMEOUT,
                                     f"Written {args.count} sample(s) but publication_matched_status.current_count=0 after {args.timeout}s (no matched DataReader on topic '{args.topic}')",
                                     stage="match")
        except Exception as we:
            logger.error(f"[!] DDS Write error: {we}")
            return format_result("dds", args.mode, STATUS_PROTO_ERROR, f"Write error: {we}", stage="write")

    # LISTEN or BOTH
    if args.mode in ["listen", "both"] and reader:
        logger.info(f"[*] Listening for DDS samples on topic '{args.topic}' for {args.duration}s...")
        t_start = time.time()
        received_count = 0
        while time.time() - t_start < args.duration:
            samples = reader.take()
            for s in samples:
                received_count += 1
                logger.info(f"[{datetime.now().isoformat()}] <=== [DDS SAMPLE RECEIVED] device_id='{s.device_id}', temp={s.temperature}, hum={s.humidity}, status='{s.status}'")
            time.sleep(0.2)

        if received_count == 0:
            logger.info(f"[*] Khong nhan duoc DDS sample nao sau {args.duration} giay (Platform/Domain {args.domain_id} chua co publisher khac).")

    logger.info("[*] DDS Simulator finished cleanly.")
    if args.mode == "listen":
        if received_count == 0:
            detail = f"Recv:0 (no downlink received) after {args.duration}s"
        else:
            detail = f"Recv:{received_count} sample(s) received"
    elif args.mode == "both":
        if received_count == 0:
            detail = f"Sent:{args.count}, Recv:0 (no downlink received)"
        else:
            detail = f"Sent:{args.count}, Recv:{received_count}"
    else:
        detail = "Completed cleanly"
    return format_result("dds", args.mode, STATUS_OK, detail, stage="channel")


if __name__ == "__main__":
    sys.exit(main())
