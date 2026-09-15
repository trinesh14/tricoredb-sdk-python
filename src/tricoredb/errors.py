"""The exception hierarchy, and how server refusals become typed errors."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .types import Response

#: The code a not-leader refusal carries. Branch on this, not on the message.
NOT_LEADER = "not_leader"


class TriCoreError(Exception):
    """Any failure from the server or the transport.

    ``code`` is the server's machine-readable reason (``diagnostics.error_code``,
    or an ``ERROR`` frame's own code), ``None`` when none was sent.
    ``leader_hint`` is the ``host:port`` of the leader, present only with
    ``code == NOT_LEADER`` and only when the cluster knows an address for it.

    The positional signature stays ``*args`` so the subclasses that also inherit
    ``OSError`` keep accepting the ``(errno, strerror)`` form.
    """

    #: Set when the request never left this process (a local refusal).
    not_sent: bool = False

    def __init__(
        self,
        *args: Any,
        code: Optional[str] = None,
        leader_hint: Optional[str] = None,
    ) -> None:
        super().__init__(*args)
        self.code: Optional[str] = code
        self.leader_hint: Optional[str] = leader_hint

    @property
    def is_redirect(self) -> bool:
        """True for every ``not_leader`` refusal, with or without a hint.

        The driver never follows the redirect itself: the hinted address may
        not be reachable from here, a new connection must authenticate again,
        and an open session transaction cannot move to another node.
        """
        return self.code == NOT_LEADER


class AuthError(TriCoreError):
    """Authentication was refused."""


class ProtocolError(TriCoreError):
    """The peer did not speak the protocol we expect."""


class PoolTimeout(TriCoreError):
    """No pooled connection became available within the timeout."""


class ConnectionFailed(TriCoreError, OSError):
    """The connection could not be established (refused, timed out, TLS refused).

    Also an ``OSError``, so existing ``except OSError`` handlers keep working.
    """


class Timeout(TriCoreError, OSError):
    """A socket read or write missed the connection's deadline.

    Fatal to the connection: the late reply could otherwise be read as the
    answer to the next request. Also an ``OSError``.
    """


def error_text(body: Any) -> str:
    if isinstance(body, dict):
        return str(body.get("message") or body.get("error") or json.dumps(body))
    return str(body)


def error_frame(cls: type[TriCoreError], body: Any) -> TriCoreError:
    """The typed error for an ``ERROR`` frame, carrying its ``code`` when present."""
    code = body.get("code") if isinstance(body, dict) else None
    return cls(error_text(body), code=code if isinstance(code, str) and code else None)


def message_of(data: Any) -> Optional[str]:
    if isinstance(data, dict):
        if "Message" in data:
            return str(data["Message"])
        if "Json" in data:
            return json.dumps(data["Json"])
    return None


def server_refusal(resp: Response, txn_open: bool) -> TriCoreError:
    """The typed error for a response whose status is not ``ok``."""
    message = f"{message_of(resp.data) or 'request failed'} (server status: {resp.status})"
    code = resp.error_code
    leader_hint = resp.leader_hint
    if code == NOT_LEADER:
        if leader_hint:
            message += (
                f" [{NOT_LEADER}: this node is not the leader; the leader serves clients "
                f"at `{leader_hint}`. This driver does not follow the hint on its own: a "
                "new connection authenticates again, and only you know whether that address "
                "is reachable from here. Send this request there."
            )
        else:
            message += (
                f" [{NOT_LEADER}: this node is not the leader and there is no address to "
                "name (an election is in progress, this node has no [raft] configured, or "
                "the leader has no address in [[raft.peers]]). There is nowhere to redirect "
                "to: wait and try again."
            )
        if txn_open:
            message += (
                " The open session transaction is over: it is bound to this connection "
                "and cannot be continued, committed or resumed on another node."
            )
        message += "]"
    return TriCoreError(message, code=code, leader_hint=leader_hint)
