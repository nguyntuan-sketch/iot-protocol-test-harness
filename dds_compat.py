# ===========================================================
# DDS Compatibility Shim for Python 3.14
# File: dds_compat.py
# 
# ISSUE: cyclonedds has no pre-built wheel for Python 3.14
# (latest wheels are cp310–cp313 as of cyclonedds 11.0.1)
#
# SOLUTIONS (choose one):
#   Option A: Use Python 3.12 with conda:
#     conda create -n iot-test python=3.12
#     conda activate iot-test
#     pip install cyclonedds
#
#   Option B: Use this shim module (stubs only — no real DDS).
#             The test will be marked SKIP with reason.
#
# This file provides stub classes so other tests can still run.
# ===========================================================

import sys
import warnings

CYCLONEDDS_AVAILABLE = False
CYCLONEDDS_ERROR = None

try:
    import cyclonedds
    from cyclonedds.domain import DomainParticipant
    from cyclonedds.topic import Topic
    from cyclonedds.pub import Publisher, DataWriter
    from cyclonedds.core import Qos, Policy
    from cyclonedds.idl import IdlStruct
    CYCLONEDDS_AVAILABLE = True
except ImportError as e:
    CYCLONEDDS_ERROR = str(e)
    warnings.warn(
        f"cyclonedds not available on Python {sys.version_info.major}.{sys.version_info.minor}: {e}. "
        "DDS tests will be SKIPPED. "
        "To enable DDS testing: use Python 3.12 with 'conda create -n iot-test python=3.12 && pip install cyclonedds'",
        ImportWarning,
        stacklevel=2
    )

    # Stub classes for graceful degradation
    class DomainParticipant:  # noqa
        def __init__(self, *args, **kwargs):
            raise NotImplementedError("cyclonedds not installed")

    class Topic:  # noqa
        def __init__(self, *args, **kwargs):
            raise NotImplementedError("cyclonedds not installed")

    class DataWriter:  # noqa
        def __init__(self, *args, **kwargs):
            raise NotImplementedError("cyclonedds not installed")

    class IdlStruct:  # noqa
        pass