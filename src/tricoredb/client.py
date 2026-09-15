"""The connection: handshake, transport, SQL, transactions and admin."""

from __future__ import annotations

import contextlib
import json
import socket
import threading
from collections.abc import Iterator, Sequence
from typing import Any, Optional

from . import _frame as frame
from ._handshake import (
    FEATURE_SERVER_PARAMS,
    FEATURE_SESSION_TXN,
    FEATURES,
    SERVER_PARAMS_NOT_GRANTED,
    SESSION_TXN_NOT_GRANTED,
    Features,
    granted_features,
    hello_payload,
)
from .cache import CacheMixin
from .document import DocumentMixin
from .errors import (
    AuthError,
    ConnectionFailed,
    ProtocolError,
    Timeout,
    TriCoreError,
    error_frame,
    server_refusal,
)
from .graph import GraphMixin
from .llm import LlmMixin
from .params import Statement, sql_param, transaction_script
from .tls import TlsOptions
from .types import Response, Rows, kind
from .vector import VectorMixin


class TriCore(CacheMixin, DocumentMixin, VectorMixin, GraphMixin, LlmMixin):
    """A connection to a TriCoreDB server.

    Not thread-safe: a connection is one request/response stream. Overlapping
    use from two threads is refused by name; use one connection per thread or
    a :class:`~tricoredb.Pool`.
    """

    def __init__(self, sock: Optional[socket.socket]) -> None:
        self._sock: Optional[socket.socket] = sock
        self._buf = b""
        self._rid = 0
        self._rid_prefix = frame.next_connection_prefix()
        self._fatal: Optional[TriCoreError] = None
        self._read_timeout: Optional[float] = sock.gettimeout() if sock is not None else None
        self.session_id: Optional[str] = None
        #: A server-side deadline in milliseconds for every request, or ``None``.
        self.request_timeout_ms: Optional[int] = None
        #: The ``request_id`` most recently sent; pass it to :meth:`cancel`.
        self.last_request_id: Optional[str] = None
        #: The feature bitmap the server granted in ``HELLO_OK``.
        self.granted_features: int = 0
        self._txn_open = False
        self._in_flight = threading.Lock()

    @property
    def features(self) -> Features:
        """What the server granted in ``HELLO_OK``, by name."""
        return Features(self.granted_features)

    @property
    def in_transaction(self) -> bool:
        """True while a :meth:`begin` block is open on this connection."""
        return self._txn_open and self._sock is not None

    @classmethod
    def connect(
        cls,
        host: str = "127.0.0.1",
        port: int = frame.DEFAULT_PORT,
        user: Optional[str] = None,
        secret: Optional[str] = None,
        client_name: str = "tricoredb-python",
        timeout: Optional[float] = 10.0,
        tls: Optional[TlsOptions] = None,
        read_timeout: Optional[float] = None,
        features: int = FEATURES,
    ) -> TriCore:
        """Connect, handshake and, when ``user`` is given, authenticate.

        ``timeout`` bounds establishing the connection (TCP, TLS, ``HELLO``,
        ``AUTH``). ``read_timeout`` is the steady-state socket deadline and
        defaults to ``None``: a read legitimately waits as long as a statement
        runs. To make the *server* stop, set :attr:`request_timeout_ms`.

        ``features`` is the capability bitmap announced in ``HELLO``; mask a bit
        out to opt out of it.
        """
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as e:
            raise ConnectionFailed(f"connect to {host}:{port} failed: {e}") from e
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if tls is not None:
            try:
                sock = tls.wrap(sock)
            except OSError as e:
                sock.close()
                raise ConnectionFailed(f"tls handshake with {host}:{port} failed: {e}") from e
        db = cls(sock)
        try:
            db._hello(client_name, features)
            if user is not None:
                db.auth(user, secret or "")
        except BaseException:
            db._drop_socket()
            raise
        db._read_timeout = read_timeout
        sock.settimeout(read_timeout)
        return db

    def close(self) -> None:
        """Say goodbye (bounded by a short deadline), then drop the socket."""
        if self._sock is None:
            return
        try:
            self._sock.settimeout(frame.CLOSE_TIMEOUT_SECONDS)
            self._send(frame.CLOSE, None)
            self._recv()
        except (OSError, TriCoreError):
            pass
        finally:
            self._drop_socket()

    def __enter__(self) -> TriCore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def ping(self) -> None:
        """A transport-level PING/PONG. Never reaches a module."""
        tag, _ = self._exchange(frame.PING, None)
        if tag != frame.PONG:
            raise ProtocolError(f"expected PONG, got tag {tag}")

    def auth(self, user: str, secret: str) -> None:
        tag, body = self._exchange(frame.AUTH, {"username": user, "secret": list(secret.encode())})
        if tag == frame.ERROR:
            raise error_frame(AuthError, body)
        if tag != frame.AUTH_OK:
            raise ProtocolError(f"expected AUTH_OK, got tag {tag}")
        if not isinstance(body, dict) or not body.get("ok"):
            message = body.get("message") if isinstance(body, dict) else None
            raise AuthError(message or "authentication refused")
        self.session_id = body.get("session_id")

    def request(self, op: dict[str, Any], database: str = "main") -> Response:
        """Send a raw operation. Every typed method funnels through here.

        Any status other than ``ok`` (``error``, ``not_implemented``, or one
        added later) raises :class:`TriCoreError` carrying ``code`` and
        ``leader_hint``.
        """
        self._rid += 1
        request_id = f"py-{self._rid_prefix}-{self._rid}"
        self.last_request_id = request_id
        payload: dict[str, Any] = {"request_id": request_id, "database": database, "op": op}
        if self.request_timeout_ms is not None:
            payload["options"] = {"timeout_ms": self.request_timeout_ms}
        tag, body = self._exchange(frame.REQUEST, payload)
        if tag == frame.ERROR:
            raise error_frame(TriCoreError, body)
        if tag != frame.RESPONSE or not isinstance(body, dict):
            raise ProtocolError(f"expected RESPONSE, got tag {tag}")
        resp = Response(body)
        if resp.status != "ok":
            raise server_refusal(resp, self._txn_open)
        return resp

    def cancel(self, request_id: str) -> int:
        """Stop one of this principal's running statements; returns how many stopped.

        Send it on a second connection: the one running the statement is busy
        reading its reply.
        """
        tag, body = self._exchange(frame.CANCEL, {"request_id": request_id})
        if tag == frame.ERROR:
            raise error_frame(TriCoreError, body)
        if tag != frame.CANCEL_OK:
            raise ProtocolError(f"expected CANCEL_OK, got tag {tag}")
        return int((body or {}).get("cancelled", 0))

    def execute(self, sql: str, args: Optional[Sequence[Any]] = None,
                database: str = "main") -> Response:
        """Run a write, DDL or script. ``args`` binds ``?`` placeholders server-side."""
        return self.request({"Sql": {"Exec": self._sql_body(sql, args)}}, database)

    def query(self, sql: str, args: Optional[Sequence[Any]] = None,
              database: str = "main") -> Rows:
        """Run a read (``SELECT`` only) and return its rows."""
        data = self.request({"Sql": {"Query": self._sql_body(sql, args)}}, database).data
        if isinstance(data, dict) and "Rows" in data:
            r = data["Rows"]
            return Rows(r.get("columns", []), r.get("rows", []))
        raise ProtocolError(f"expected Rows, got {kind(data)}")

    def _sql_body(self, sql: str, args: Optional[Sequence[Any]]) -> dict[str, Any]:
        if args is None:
            return {"sql": sql}
        if not self.granted_features & FEATURE_SERVER_PARAMS:
            err = TriCoreError(SERVER_PARAMS_NOT_GRANTED)
            err.not_sent = True
            raise err
        return {"sql": sql, "params": [sql_param(a) for a in args]}

    def admin_ping(self, database: str = "main") -> None:
        """Round-trip through the full pipeline. Needs Admin and the cluster module."""
        self.request({"Admin": "Ping"}, database)

    def admin_status(self, database: str = "main") -> dict[str, Any]:
        """Server status as reported by the cluster core."""
        data = self.request({"Admin": "Status"}, database).data
        if isinstance(data, dict) and "Message" in data:
            return {"message": data["Message"]}
        if isinstance(data, dict) and "Json" in data:
            return dict(data["Json"])
        raise ProtocolError(f"expected Json for admin status, got {kind(data)}")

    def transaction(self, statements: Sequence[Statement], database: str = "main") -> dict[str, Any]:
        """Run a whole ``BEGIN ... COMMIT`` script atomically in one request.

        Each entry is ``sql`` or ``(sql, args)``. The script is one SQL string,
        so ``args`` are rendered client-side with :func:`~tricoredb.bind_params`.
        Works on every node, including ones that withhold ``SESSION_TXN``.
        """
        data = self.execute(transaction_script(statements), database=database).data
        if isinstance(data, dict) and "Json" in data:
            return dict(data["Json"])
        raise ProtocolError(f"expected Json for transaction, got {kind(data)}")

    def begin(self, database: str = "main") -> dict[str, Any]:
        """Open a session transaction bound to this connection's socket.

        Requires ``SESSION_TXN``; without it this raises by name and sends
        nothing (it never autocommits). The server's in-block rules apply: a
        failed statement aborts the block, no nesting, no DDL, an idle block is
        rolled back.
        """
        if not self.granted_features & FEATURE_SESSION_TXN:
            err = TriCoreError(SESSION_TXN_NOT_GRANTED)
            err.not_sent = True
            raise err
        return self._txn_control("BEGIN", database)

    def commit(self, database: str = "main") -> dict[str, Any]:
        """Commit the open block. Any reply, refusal included, ends the block."""
        return self._txn_control("COMMIT", database)

    def rollback(self, database: str = "main") -> dict[str, Any]:
        """Discard the open block."""
        return self._txn_control("ROLLBACK", database)

    @contextlib.contextmanager
    def transaction_block(self, database: str = "main") -> Iterator[TriCore]:
        """``begin()``, run the block, ``commit()``; on an exception, ``rollback()``
        and re-raise the original. Every statement must go through the yielded
        connection."""
        self.begin(database)
        try:
            yield self
        except BaseException:
            if self.in_transaction:
                try:
                    self.rollback(database)
                except Exception:
                    pass
            raise
        self.commit(database)

    def _txn_control(self, keyword: str, database: str) -> dict[str, Any]:
        try:
            resp = self.request({"Sql": {"Exec": {"sql": keyword}}}, database)
        except BaseException as e:
            if keyword != "BEGIN" and not getattr(e, "not_sent", False):
                self._txn_open = False
            raise
        self._txn_open = keyword == "BEGIN"
        data = resp.data
        if isinstance(data, dict) and "Json" in data:
            return dict(data["Json"])
        raise ProtocolError(f"expected Json for {keyword}, got {kind(data)}")

    def _hello(self, client_name: str, features: int = FEATURES) -> None:
        tag, body = self._exchange(frame.HELLO, hello_payload(client_name, features))
        if tag == frame.ERROR:
            raise error_frame(ProtocolError, body)
        if tag != frame.HELLO_OK:
            raise ProtocolError(f"expected HELLO_OK, got tag {tag}")
        if not isinstance(body, dict) or not body.get("ok"):
            message = body.get("message") if isinstance(body, dict) else None
            raise ProtocolError(message or "handshake refused")
        self.granted_features = granted_features(body)

    def _exchange(self, tag: int, payload: Any) -> tuple[int, Any]:
        """Send one frame and read its reply while holding the connection.

        A second overlapping caller is refused rather than queued: interleaved
        frames would desynchronise the stream.
        """
        if not self._in_flight.acquire(blocking=False):
            err = TriCoreError(
                "a request is already in flight on this connection. A TriCore connection "
                "is a single request/response stream: overlapping requests interleave "
                "frames and deadlock. Use one connection per thread, or a Pool."
            )
            err.not_sent = True
            raise err
        try:
            self._send(tag, payload)
            return self._recv()
        finally:
            self._in_flight.release()

    def _send(self, tag: int, payload: Any) -> None:
        if self._fatal is not None:
            raise self._fatal
        if self._sock is None:
            raise TriCoreError("connection is closed")
        body = b"" if payload is None else frame.encode_body(payload)
        # An oversized REQUEST is left for the server to refuse by name.
        if tag != frame.REQUEST and len(body) > frame.MAX_CONTROL_FRAME_SIZE:
            raise ProtocolError(
                f"refusing to send a {len(body)}-byte control frame (tag {tag}); "
                f"the protocol caps control frames at {frame.MAX_CONTROL_FRAME_SIZE} bytes"
            )
        try:
            self._sock.sendall(frame.HEADER.pack(frame.VERSION, tag, len(body)) + body)
        except socket.timeout as e:
            raise self._poison(Timeout(f"write timed out after {self._read_timeout}s")) from e

    def _recv(self) -> tuple[int, Any]:
        head = self._read_exactly(frame.HEADER.size)
        version, tag, length = frame.HEADER.unpack(head)
        # Checked before any payload byte is read; either failure poisons the
        # connection because a length-prefixed stream cannot resynchronise.
        if version > frame.MAX_SUPPORTED_FRAME_VERSION:
            raise self._poison(ProtocolError(
                f"frame header version {version} is newer than this driver can read "
                f"(max {frame.MAX_SUPPORTED_FRAME_VERSION})"
            ))
        limit = frame.max_payload_for(tag)
        if length > limit:
            raise self._poison(ProtocolError(
                f"frame (tag {tag}) declares a {length}-byte payload, above the "
                f"{limit}-byte limit for that frame; refusing to buffer it"
            ))
        body = self._read_exactly(length) if length else b""
        return tag, (frame_json(body) if body else None)

    def _poison(self, err: TriCoreError) -> TriCoreError:
        """Record ``err`` as fatal, drop the socket, and return the error to raise."""
        if self._fatal is None:
            self._fatal = err
        self._drop_socket()
        return err

    def _drop_socket(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _read_exactly(self, n: int) -> bytes:
        """Read exactly ``n`` bytes, buffering greedily so a frame usually costs one recv."""
        if self._fatal is not None:
            raise self._fatal
        if self._sock is None:
            raise TriCoreError("connection is closed")
        while len(self._buf) < n:
            try:
                chunk = self._sock.recv(max(n - len(self._buf), 8192))
            except socket.timeout as e:
                raise self._poison(Timeout(f"read timed out after {self._read_timeout}s")) from e
            if not chunk:
                raise self._poison(ProtocolError("connection closed mid-frame by the server"))
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out


def frame_json(body: bytes) -> Any:
    import json

    return json.loads(body)
