"""Protocol and resource hardening.

Frame ceilings, header version, deadlines and handshake integrity use a
misbehaving in-process peer. Request-id uniqueness, CANCEL scoping and
incomplete-response handling run against a real server.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

import pytest
from _peers import ERROR, HEADER, HELLO_OK, AUTH_OK, PONG, RESPONSE, HostilePeer, ScriptedPeer, frame

from tricoredb import (
    MAX_CONTROL_FRAME_SIZE,
    MAX_FRAME_SIZE,
    AuthError,
    Pool,
    ProtocolError,
    Timeout,
    TriCore,
    TriCoreError,
)


def raises_within(fn: Callable[[], Any], want: type[BaseException], within: float) -> BaseException:
    """``fn()`` must raise ``want``; returning or hanging past ``within`` fails."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(within)
    assert not t.is_alive(), f"still waiting after {within}s (it hung)"
    assert "error" in box, f"returned {box.get('value')!r} instead of raising"
    assert isinstance(box["error"], want), f"raised {box['error']!r}, not {want.__name__}"
    return box["error"]


def header_only(version: int, tag: int, declared: int) -> bytes:
    return HEADER.pack(version, tag, declared)


@pytest.mark.parametrize("declared", [0xFFFFFFFF, MAX_FRAME_SIZE + 1])
def test_oversized_response_is_refused_not_buffered(declared: int) -> None:
    with HostilePeer(lambda: header_only(1, RESPONSE, declared)) as peer:
        db = TriCore.connect("127.0.0.1", peer.port)
        raises_within(db.ping, ProtocolError, 5)
        db.close()


def test_control_frame_above_64k_is_refused() -> None:
    with HostilePeer(lambda: header_only(1, ERROR, MAX_CONTROL_FRAME_SIZE + 1)) as peer:
        db = TriCore.connect("127.0.0.1", peer.port)
        raises_within(db.ping, ProtocolError, 5)
        db.close()


def test_unknown_frame_version_is_refused_by_name() -> None:
    with HostilePeer(lambda: header_only(99, PONG, 0)) as peer:
        db = TriCore.connect("127.0.0.1", peer.port)
        e = raises_within(db.ping, ProtocolError, 5)
        assert "version 99" in str(e)
        db.close()


def test_large_frame_below_the_ceiling_still_arrives() -> None:
    big = b'{"request_id":"srv-1","status":"ok","data":{"Message":"' + b"x" * (2 << 20) + b'"},"diagnostics":{}}'
    with HostilePeer(lambda: HEADER.pack(1, RESPONSE, len(big)) + big) as peer:
        db = TriCore.connect("127.0.0.1", peer.port)
        assert len(db.request({"Admin": "Ping"}).data["Message"]) == 2 << 20
        db.close()


def test_refused_frame_poisons_the_connection() -> None:
    with HostilePeer(lambda: header_only(1, RESPONSE, 0xFFFFFFFF)) as peer:
        db = TriCore.connect("127.0.0.1", peer.port)
        raises_within(db.ping, ProtocolError, 5)
        raises_within(db.ping, TriCoreError, 5)
        db.close()


def test_read_deadline_is_a_typed_fatal_timeout() -> None:
    with HostilePeer(None) as peer:
        db = TriCore.connect("127.0.0.1", peer.port, read_timeout=1.0)
        e = raises_within(db.ping, Timeout, 4)
        assert isinstance(e, OSError)
        raises_within(db.ping, Timeout, 2)
        db.close()


def test_connect_timeout_is_not_a_read_deadline() -> None:
    with HostilePeer(None) as peer:
        db = TriCore.connect("127.0.0.1", peer.port, timeout=1.0)

        def swallow() -> None:
            try:
                db.ping()
            except BaseException:  # noqa: BLE001
                pass

        t = threading.Thread(target=swallow, daemon=True)
        t.start()
        t.join(3.0)
        assert t.is_alive(), "a steady-state read died at the connect timeout"
        db.close()


def test_hello_ok_with_ok_false_is_a_refusal() -> None:
    with ScriptedPeer([frame(HELLO_OK, {"ok": False, "message": "protocol mismatch"})]) as peer:
        raises_within(lambda: TriCore.connect("127.0.0.1", peer.port), ProtocolError, 5)


def test_auth_ok_with_ok_false_is_a_refusal_without_the_secret() -> None:
    script = [frame(HELLO_OK, {"ok": True, "features": 0}), frame(AUTH_OK, {"ok": False, "message": "bad password"})]
    with ScriptedPeer(script) as peer:
        e = raises_within(lambda: TriCore.connect("127.0.0.1", peer.port, user="u", secret="s3cr3t"), AuthError, 5)
        assert "s3cr3t" not in str(e)


def test_missing_features_field_grants_nothing() -> None:
    with ScriptedPeer([frame(HELLO_OK, {"ok": True})]) as peer:
        db = TriCore.connect("127.0.0.1", peer.port)
        assert db.granted_features == 0
        db.close()


def test_connection_refused_is_connection_failed() -> None:
    with HostilePeer(None) as peer:
        port = peer.port
    with pytest.raises(ConnectionFailedOrOSError) as ei:
        TriCore.connect("127.0.0.1", port, timeout=2.0)
    assert isinstance(ei.value, TriCoreError) and isinstance(ei.value, OSError)
    assert f"127.0.0.1:{port}" in str(ei.value)


def test_overlapping_use_is_refused_not_interleaved() -> None:
    with HostilePeer(None) as peer:
        db = TriCore.connect("127.0.0.1", peer.port)
        t = threading.Thread(target=lambda: _swallow(db.ping), daemon=True)
        t.start()
        time.sleep(0.3)
        with pytest.raises(TriCoreError, match="already in flight") as ei:
            db.ping()
        assert ei.value.not_sent
        db.close()


def _swallow(fn: Callable[[], Any]) -> None:
    try:
        fn()
    except BaseException:  # noqa: BLE001
        pass


from tricoredb import ConnectionFailed as ConnectionFailedOrOSError  # noqa: E402


# -- live server -------------------------------------------------------------

pytestmark_live = pytest.mark.live


@pytest.mark.live
def test_unauthenticated_connection_reaches_nothing(server: Any) -> None:
    db = TriCore.connect(server.host, server.port)
    assert db.session_id is None
    db.ping()
    with pytest.raises(TriCoreError):
        db.request({"Admin": "Ping"})
    with pytest.raises(TriCoreError):
        db.cancel("anything")
    db.close()


@pytest.mark.live
def test_request_ids_are_unique_across_one_principal(server: Any) -> None:
    opts = dict(host=server.host, port=server.port, user="alice", secret="pw")
    a, b = TriCore.connect(**opts), TriCore.connect(**opts)  # type: ignore[arg-type]
    ra, rb = a.request({"Admin": "Ping"}), b.request({"Admin": "Ping"})
    assert ra.request_id and rb.request_id and ra.request_id != rb.request_id
    a.close()
    b.close()

    pool = Pool(size=6, **opts)  # type: ignore[arg-type]
    seen: list[str] = []
    lock = threading.Lock()

    def one() -> None:
        with pool.get(timeout=30) as db:
            r = db.request({"Admin": "Ping"})
            with lock:
                seen.append(r.request_id)

    ts = [threading.Thread(target=one) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)
    pool.close()
    assert len(set(seen)) == 6


ROWS = 100_000
SLOW_SCAN = "SELECT * FROM hard_t WHERE name = 'no-such-row'"


@pytest.mark.live
def test_cancel_stops_exactly_one_statement(server: Any) -> None:
    opts = dict(host=server.host, port=server.port, user="alice", secret="pw")
    setup = TriCore.connect(**opts)  # type: ignore[arg-type]
    setup.execute("CREATE TABLE hard_t (id INT PRIMARY KEY, name TEXT)")
    for base in range(0, ROWS, 2000):
        setup.transaction([f"INSERT INTO hard_t (id, name) VALUES ({i}, 'row{i}')"
                           for i in range(base, min(base + 2000, ROWS))])
    setup.close()

    workers = [TriCore.connect(**opts), TriCore.connect(**opts)]  # type: ignore[arg-type]
    stopped = [0, 0]
    running = [True]
    errors: list[str] = []

    def scan_loop(slot: int) -> None:
        while running[0]:
            try:
                workers[slot].query(SLOW_SCAN)
            except TriCoreError as e:
                if "cancel" not in str(e).lower():
                    errors.append(str(e))
                else:
                    stopped[slot] += 1
                running[0] = False

    threads = [threading.Thread(target=scan_loop, args=(i,), daemon=True) for i in (0, 1)]
    for t in threads:
        t.start()
    canceller = TriCore.connect(**opts)  # type: ignore[arg-type]
    cancelled, until = 0, time.monotonic() + 30
    while cancelled == 0 and time.monotonic() < until:
        rid = workers[0].last_request_id
        if rid:
            cancelled = canceller.cancel(rid)
        else:
            time.sleep(0.001)
    running[0] = False
    canceller.close()
    for t in threads:
        t.join(timeout=60)
    for w in workers:
        w.close()
    assert not errors, errors
    assert cancelled == 1
    assert stopped == [1, 0]


@pytest.mark.live
def test_not_implemented_status_is_never_returned_as_success(db: TriCore) -> None:
    with pytest.raises(TriCoreError, match="not_implemented"):
        db.request({"Admin": "RebalanceStatus"})
    with pytest.raises(TriCoreError):
        db.request({"Admin": {"RaftMessage": {"from": "nobody", "payload": [1]}}})
    assert db.request({"Admin": "Ping"}).status == "ok"


_ = Optional
