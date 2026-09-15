"""What the driver does with a ``not_leader`` refusal.

A single node never refuses as not-the-leader, so the refusal comes from an
in-process peer returning the envelope the server's request pipeline builds.
This proves the driver's half: the typed error is right and nothing is re-sent.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest
from _peers import EnvelopePeer

from tricoredb import NOT_LEADER, Pool, Response, TriCore, TriCoreError

LEADER_ADDR = "10.9.9.7:8427"


def not_leader_envelope(request_id: str, leader: Optional[str]) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "module": None, "region_id": "local", "route": "", "cache": "",
        "elapsed_ms": 0, "warnings": [], "error_code": NOT_LEADER,
    }
    if leader is not None:
        diagnostics["leader_hint"] = leader
    return {
        "request_id": request_id,
        "status": "error",
        "data": {"Message": "not the raft leader — send writes to `n2`"},
        "diagnostics": diagnostics,
    }


def ok_envelope(request_id: str, payload: Any) -> dict[str, Any]:
    return {
        "request_id": request_id, "status": "ok", "data": {"Json": payload},
        "diagnostics": {"module": None, "region_id": "local", "route": "", "cache": "",
                        "elapsed_ms": 0, "warnings": []},
    }


def test_response_exposes_code_hint_and_redirect() -> None:
    redirect = Response(not_leader_envelope("r1", LEADER_ADDR))
    assert (redirect.error_code, redirect.leader_hint, redirect.is_redirect) == (NOT_LEADER, LEADER_ADDR, True)
    election = Response(not_leader_envelope("r1", None))
    assert (election.leader_hint, election.is_redirect) == (None, True)
    fine = Response(ok_envelope("r1", {"ok": True}))
    assert (fine.error_code, fine.is_redirect) == (None, False)


def test_redirect_is_typed_and_not_followed() -> None:
    with EnvelopePeer(lambda rid, n, body: not_leader_envelope(rid, LEADER_ADDR)) as peer:
        db = TriCore.connect(port=peer.port)
        with pytest.raises(TriCoreError) as ei:
            db.execute("INSERT INTO t VALUES (1, 'ada')")
        e = ei.value
        assert e.code == "not_leader" == NOT_LEADER
        assert e.leader_hint == LEADER_ADDR
        assert e.is_redirect is True
        assert "not the raft leader — send writes to `n2`" in str(e)
        assert LEADER_ADDR in str(e) and "n2" not in e.leader_hint
        assert peer.connections == 1, "the driver dialled nothing else"
        assert peer.requests == 1, "the write was re-sent nowhere"
        db.close()


def test_no_leader_to_name_means_wait() -> None:
    with EnvelopePeer(lambda rid, n, body: not_leader_envelope(rid, None)) as peer:
        db = TriCore.connect(port=peer.port)
        with pytest.raises(TriCoreError) as ei:
            db.execute("INSERT INTO t VALUES (1)")
        assert (ei.value.code, ei.value.leader_hint, ei.value.is_redirect) == ("not_leader", None, True)
        assert "wait and try again" in str(ei.value)
        assert peer.requests == 1
        db.close()


def test_other_refusals_are_not_redirects() -> None:
    def capacity(rid: str, n: int, body: Any) -> dict[str, Any]:
        env = not_leader_envelope(rid, None)
        env["diagnostics"]["error_code"] = "storage_capacity"
        env["data"] = {"Message": "index memory limit reached"}
        return env

    with EnvelopePeer(capacity) as peer:
        db = TriCore.connect(port=peer.port)
        with pytest.raises(TriCoreError) as ei:
            db.execute("INSERT INTO t VALUES (1)")
        assert (ei.value.code, ei.value.is_redirect, ei.value.leader_hint) == ("storage_capacity", False, None)
        db.close()


def test_uncoded_refusal_message_is_unchanged() -> None:
    def uncoded(rid: str, n: int, body: Any) -> dict[str, Any]:
        env = not_leader_envelope(rid, None)
        del env["diagnostics"]["error_code"]
        env["data"] = {"Message": "no such table: t"}
        return env

    with EnvelopePeer(uncoded) as peer:
        db = TriCore.connect(port=peer.port)
        with pytest.raises(TriCoreError) as ei:
            db.execute("INSERT INTO t VALUES (1)")
        assert ei.value.code is None and ei.value.is_redirect is False
        assert str(ei.value) == "no such table: t (server status: error)"
        db.close()


def test_redirect_inside_session_transaction_ends_the_block_loudly() -> None:
    def reply(rid: str, n: int, body: Any) -> dict[str, Any]:
        return ok_envelope(rid, {"transaction": "began"}) if n == 1 else not_leader_envelope(rid, LEADER_ADDR)

    with EnvelopePeer(reply) as peer:
        db = TriCore.connect(port=peer.port)
        db.begin()
        assert db.in_transaction
        with pytest.raises(TriCoreError) as ei:
            db.execute("INSERT INTO t VALUES (1, 'ada')")
        assert ei.value.code == "not_leader"
        assert "session transaction is over" in str(ei.value)
        assert peer.connections == 1 and peer.requests == 2
        db.close()


def test_coded_refusal_does_not_retire_a_pooled_connection() -> None:
    with EnvelopePeer(lambda rid, n, body: not_leader_envelope(rid, LEADER_ADDR)) as peer:
        pool = Pool(port=peer.port, size=2)
        with pytest.raises(TriCoreError):
            with pool.get() as db:
                db.execute("INSERT INTO t VALUES (1)")
        assert pool.stats()["created"] == 1 and pool.stats()["idle"] == 1
        assert peer.connections == 1
        pool.close()
