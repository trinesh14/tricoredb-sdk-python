"""A thread-safe connection pool."""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterator
from typing import Optional

from ._frame import DEFAULT_PORT
from .client import TriCore
from .errors import PoolTimeout, ProtocolError, TriCoreError
from .tls import TlsOptions


class Pool:
    """Authenticated connections handed out with exclusive ownership.

    A connection is one request/response stream, so concurrency needs one
    connection per concurrent caller. ``get()`` is a context manager so a
    borrowed connection cannot be shared by accident::

        with Pool("127.0.0.1", 8427, user="admin", secret="pw", size=8) as pool:
            with pool.get() as db:
                db.execute("INSERT INTO t VALUES (1, 'ada')")

    Connections are created lazily, never beyond ``size``.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        user: Optional[str] = None,
        secret: Optional[str] = None,
        size: int = 8,
        timeout: Optional[float] = 10.0,
        client_name: str = "tricoredb-python-pool",
        tls: Optional[TlsOptions] = None,
        read_timeout: Optional[float] = None,
    ) -> None:
        if size < 1:
            raise ValueError("pool size must be >= 1")
        self._host, self._port = host, port
        self._user, self._secret = user, secret
        self._timeout, self._client_name = timeout, client_name
        self._read_timeout = read_timeout
        self._tls = tls
        self._size = size
        self._idle: list[TriCore] = []
        self._created = 0
        self._closed = False
        self._lock = threading.Lock()
        self._available = threading.Condition(self._lock)

    def _connect(self) -> TriCore:
        return TriCore.connect(
            self._host, self._port, user=self._user, secret=self._secret,
            client_name=self._client_name, timeout=self._timeout, tls=self._tls,
            read_timeout=self._read_timeout,
        )

    @contextlib.contextmanager
    def get(self, timeout: Optional[float] = 10.0) -> Iterator[TriCore]:
        """Borrow a connection for the block.

        Waits up to ``timeout`` seconds, then raises :class:`PoolTimeout`. A
        connection never returns to the pool inside a session transaction: if
        the block ends with one open it is rolled back and this raises; if the
        block raises with one open it is rolled back and the block's exception
        propagates. A transport or protocol failure retires the connection.
        """
        conn = self._acquire(timeout)
        broken = False
        left_open = False
        try:
            yield conn
            if conn.in_transaction:
                left_open = True
                broken = not _abandon_block(conn)
        except (OSError, ProtocolError):
            broken = True
            raise
        except BaseException:
            if conn.in_transaction:
                broken = not _abandon_block(conn)
            raise
        finally:
            self._release(conn, broken)
        if left_open:
            raise TriCoreError(
                "the block ended with a session transaction still open on the pooled "
                "connection; it has been rolled back rather than returned to the pool "
                "mid-block. commit() or rollback() inside the block, or use "
                "db.transaction_block()."
            )

    def _acquire(self, timeout: Optional[float]) -> TriCore:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._available:
            while True:
                if self._closed:
                    raise TriCoreError("pool is closed")
                if self._idle:
                    return self._idle.pop()
                if self._created < self._size:
                    self._created += 1
                    break
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise PoolTimeout(
                        f"no pooled connection available within {timeout}s "
                        f"(size={self._size}); raise size or shorten your work"
                    )
                self._available.wait(remaining)
        try:
            return self._connect()
        except Exception:
            with self._available:
                self._created -= 1
                self._available.notify()
            raise

    def _release(self, conn: TriCore, broken: bool) -> None:
        if conn.in_transaction:
            broken = True
        with self._available:
            if broken or self._closed:
                self._created -= 1
                self._available.notify()
                try:
                    conn.close()
                except Exception:
                    pass
                return
            self._idle.append(conn)
            self._available.notify()

    @property
    def size(self) -> int:
        return self._size

    def stats(self) -> dict[str, int]:
        """``size``, ``created``, ``idle`` and ``in_use``."""
        with self._lock:
            return {
                "size": self._size,
                "created": self._created,
                "idle": len(self._idle),
                "in_use": self._created - len(self._idle),
            }

    def close(self) -> None:
        """Close idle connections now; in-use ones close on release."""
        with self._available:
            self._closed = True
            idle, self._idle = self._idle, []
            self._created -= len(idle)
            self._available.notify_all()
        for c in idle:
            try:
                c.close()
            except Exception:
                pass

    def __enter__(self) -> Pool:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _abandon_block(conn: TriCore) -> bool:
    """Roll back a block a pooled connection was left in. False means retire it."""
    try:
        conn.rollback()
        return True
    except Exception:
        return False
