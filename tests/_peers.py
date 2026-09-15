"""In-process peers that speak real frame bytes, for tests a real server cannot stage."""

from __future__ import annotations

import json
import socket
import struct
import threading
from typing import Any, Callable, Optional

HEADER = struct.Struct(">BBI")
HELLO, AUTH, REQUEST, RESPONSE, PING, PONG, ERROR, CLOSE = 0, 1, 2, 3, 4, 5, 6, 7
HELLO_OK, AUTH_OK, BYE = 8, 9, 10


def frame(tag: int, obj: Any) -> bytes:
    body = b"" if obj is None else json.dumps(obj).encode()
    return HEADER.pack(1, tag, len(body)) + body


class _Listener:
    def __init__(self) -> None:
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(8)
        self.port: int = self._srv.getsockname()[1]
        self.connections = 0
        self._held: list[socket.socket] = []
        self._stop = False
        self._acceptor = threading.Thread(target=self._accept, daemon=True)
        self._acceptor.start()

    def _accept(self) -> None:
        while not self._stop:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            self.connections += 1
            self._held.append(conn)
            threading.Thread(target=self._guarded, args=(conn,), daemon=True).start()

    def _guarded(self, conn: socket.socket) -> None:
        try:
            self.session(conn)
        except OSError:
            pass

    def session(self, conn: socket.socket) -> None:
        raise NotImplementedError

    @staticmethod
    def frames(conn: socket.socket):  # type: ignore[no-untyped-def]
        buf = b""
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                return
            buf += chunk
            while len(buf) >= 6:
                _v, tag, length = HEADER.unpack(buf[:6])
                if len(buf) < 6 + length:
                    break
                body = json.loads(buf[6:6 + length]) if length else None
                buf = buf[6 + length:]
                yield tag, body

    def close(self) -> None:
        self._stop = True
        # On Linux, close() alone leaves the port listening while the accept
        # thread is blocked in accept(); shutdown() wakes it and stops listening.
        # Windows refuses shutdown() on a listening socket, but close() suffices there.
        try:
            self._srv.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._srv.close()
        except OSError:
            pass
        self._acceptor.join(timeout=2)
        for c in self._held:
            try:
                c.close()
            except OSError:
                pass

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class EnvelopePeer(_Listener):
    """Completes HELLO/AUTH and answers each REQUEST with ``reply(request_id, n, body)``."""

    def __init__(self, reply: Callable[[str, int, Any], dict[str, Any]], features: int = 1 << 2):
        self._reply = reply
        self._features = features
        self.requests = 0
        super().__init__()

    def session(self, conn: socket.socket) -> None:
        for tag, body in self.frames(conn):
            if tag == HELLO:
                conn.sendall(frame(HELLO_OK, {"ok": True, "features": self._features}))
            elif tag == AUTH:
                conn.sendall(frame(AUTH_OK, {"ok": True, "session_id": "s1"}))
            elif tag == CLOSE:
                conn.sendall(frame(BYE, {}))
            elif tag == REQUEST:
                self.requests += 1
                conn.sendall(frame(RESPONSE, self._reply(body["request_id"], self.requests, body)))


class HostilePeer(_Listener):
    """Completes HELLO, then answers every later frame with ``after()``, or nothing."""

    def __init__(self, after: Optional[Callable[[], bytes]]):
        self._after = after
        super().__init__()

    def session(self, conn: socket.socket) -> None:
        greeted = False
        for tag, _body in self.frames(conn):
            if tag == HELLO and not greeted:
                greeted = True
                conn.sendall(frame(HELLO_OK, {"ok": True, "features": 0}))
            elif self._after is not None:
                conn.sendall(self._after())


class ScriptedPeer(_Listener):
    """Answers the Nth frame received with the Nth scripted frame, HELLO included."""

    def __init__(self, script: list[bytes]):
        self._script = list(script)
        super().__init__()

    def session(self, conn: socket.socket) -> None:
        i = 0
        for _tag, _body in self.frames(conn):
            if i < len(self._script):
                conn.sendall(self._script[i])
                i += 1
