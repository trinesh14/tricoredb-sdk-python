"""Capability negotiation in ``HELLO`` / ``HELLO_OK``.

The client announces the feature bits it understands; the server replies with
the subset it granted. A server too old to negotiate omits the field, which
reads as ``0``. Anything that needs a bit checks for the bit and fails by name
when it is missing; nothing falls back silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._frame import PROTOCOL, VERSION

FEATURE_CORRELATION_ID = 1 << 0
#: The server binds ``?`` placeholders server-side.
FEATURE_SERVER_PARAMS = 1 << 1
#: Session-scoped transactions (``BEGIN`` / statements / ``COMMIT`` as separate
#: requests on one connection). A sharded or forwarding node may withhold it.
FEATURE_SESSION_TXN = 1 << 2
FEATURES = FEATURE_CORRELATION_ID | FEATURE_SERVER_PARAMS | FEATURE_SESSION_TXN


@dataclass(frozen=True)
class Features:
    """What the server granted in ``HELLO_OK``, by name.

    ``mask`` is the raw bitmap (also :attr:`TriCore.granted_features`).
    """

    mask: int = 0

    @property
    def correlation_id(self) -> bool:
        return bool(self.mask & FEATURE_CORRELATION_ID)

    @property
    def server_params(self) -> bool:
        return bool(self.mask & FEATURE_SERVER_PARAMS)

    @property
    def session_txn(self) -> bool:
        return bool(self.mask & FEATURE_SESSION_TXN)


def hello_payload(client_name: str, features: int) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "version": {"major": VERSION, "minor": 0},
        "client": client_name,
        "features": features,
    }


def granted_features(body: Any) -> int:
    """The granted bitmap. Absent means nothing was granted, never everything."""
    if not isinstance(body, dict):
        return 0
    return int(body.get("features", 0) or 0)


SERVER_PARAMS_NOT_GRANTED = (
    "this server did not grant server-side parameters (SERVER_PARAMS is not in the granted "
    "feature set), so `?` placeholders cannot be bound on this connection. Nothing was sent. "
    "Render the statement yourself with tricoredb.bind_params(sql, args) if client-side "
    "rendering is acceptable, or connect to a server that grants SERVER_PARAMS"
)

SESSION_TXN_NOT_GRANTED = (
    "this server did not grant session transactions (SESSION_TXN is not in the granted "
    "feature set), so begin()/commit()/rollback() cannot open a rollback boundary on this "
    "connection; use transaction([...]) to send the whole unit as one "
    "`BEGIN; <statements>; COMMIT` request"
)
