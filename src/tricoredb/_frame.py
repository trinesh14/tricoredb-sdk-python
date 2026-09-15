"""Frame layout, wire tags and payload ceilings of the native ``tricore`` protocol.

A frame is a six-byte header (format version, tag, big-endian ``u32`` payload
length) followed by a JSON payload.
"""

from __future__ import annotations

import json
import os
import struct
import threading
import time
from typing import Any

PROTOCOL = "tricore"
VERSION = 1
DEFAULT_PORT = 8427

#: The protocol version this SDK speaks, as ``(major, minor)``.
PROTOCOL_VERSION = (VERSION, 0)

HELLO, AUTH, REQUEST, RESPONSE = 0, 1, 2, 3
PING, PONG, ERROR, CLOSE = 4, 5, 6, 7
HELLO_OK, AUTH_OK, BYE = 8, 9, 10
CANCEL, CANCEL_OK = 11, 12

HEADER = struct.Struct(">BBI")

#: Ceilings mirrored from the Rust transport. ``REQUEST``/``RESPONSE`` carry bulk
#: data; every other frame is a small control document, including the two an
#: unauthenticated peer may send.
MAX_FRAME_SIZE = 16 * 1024 * 1024
MAX_CONTROL_FRAME_SIZE = 64 * 1024

#: The highest frame-header format version this driver can read.
MAX_SUPPORTED_FRAME_VERSION = 1

#: How long ``close()`` waits for ``BYE`` before dropping the socket anyway.
CLOSE_TIMEOUT_SECONDS = 2.0


def max_payload_for(tag: int) -> int:
    """The payload ceiling for one tag. An unknown tag gets the tighter one."""
    return MAX_FRAME_SIZE if tag in (REQUEST, RESPONSE) else MAX_CONTROL_FRAME_SIZE


def encode_body(payload: Any) -> bytes:
    """JSON for one frame body.

    Plain ``json.dumps``: Python integers are exact in JSON, and a ``Decimal``
    already travels as text (see :func:`tricoredb.params.sql_param`).
    """
    return json.dumps(payload).encode()


_PROCESS_STAMP = time.time_ns()
_PREFIX_SEQ = 0
_PREFIX_LOCK = threading.Lock()


def next_connection_prefix() -> str:
    """A ``request_id`` prefix unique to one connection.

    The server's cancel registry is keyed by ``request_id`` within a principal
    and cancels every match, so pooled connections of one principal must never
    issue the same id.
    """
    global _PREFIX_SEQ
    with _PREFIX_LOCK:
        _PREFIX_SEQ += 1
        return f"{os.getpid():x}{_PROCESS_STAMP:x}{_PREFIX_SEQ:x}"
