"""Basic connection, cache, SQL and fragmentation against a real server."""

from __future__ import annotations

from typing import Any

import pytest

from tricoredb import SqlSource, TriCore, TriCoreError

pytestmark = pytest.mark.live


def test_connect_ping_and_session(db: TriCore) -> None:
    assert db.session_id
    db.ping()
    db.cache_ping()
    db.admin_ping()
    assert isinstance(db.admin_status(), dict)


def test_cache_round_trips(db: TriCore) -> None:
    db.cache_set("pyt", "k", b"hello-python")
    assert db.cache_get("pyt", "k") == b"hello-python"
    db.cache_set("pyt", "empty", b"")
    assert db.cache_get("pyt", "empty") == b""
    assert db.cache_get("pyt", "definitely-absent") is None
    assert db.cache_delete("pyt", "k") is True
    assert db.cache_get("pyt", "k") is None
    raw = bytes(range(256))
    db.cache_set("pyt", "binary", raw)
    assert db.cache_get("pyt", "binary") == raw


def test_cache_collections(db: TriCore) -> None:
    ns = "pyt_coll"
    db.cache_clear_namespace(ns)
    assert db.cache_rpush(ns, "q", [b"a", b"b"]) == 2
    assert db.cache_lpush(ns, "q", [b"z"]) == 3
    assert db.cache_lrange(ns, "q", 0, -1) == [b"z", b"a", b"b"]
    assert db.cache_sadd(ns, "s", [b"x", b"y"]) == 2 and db.cache_sismember(ns, "s", b"y")
    assert db.cache_hset_text(ns, "h", {"name": "ada"}) == 1
    assert db.cache_hgetall(ns, "h") == [(b"name", b"ada")]
    id1 = db.cache_xadd_text(ns, "ev", {"type": "signup"})
    id2 = db.cache_xadd_text(ns, "ev", {"type": "login"})
    assert [e.text["type"] for e in db.cache_xread(ns, "ev", id1)] == ["login"]
    assert db.cache_xdel(ns, "ev", [id2]) == 1
    assert db.cache_incr(ns, "n", 5) == 5 and db.cache_incr(ns, "n", 2) == 7
    assert db.cache_set_nx(ns, "lock", b"a") is True and db.cache_set_nx(ns, "lock", b"b") is False
    assert db.cache_clear_namespace(ns) >= 5


def test_sql_basics_and_refusals(db: TriCore) -> None:
    db.execute("CREATE TABLE pyt_users (id INT PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO pyt_users VALUES (?, ?)", [1, "ada"])
    db.execute("INSERT INTO pyt_users VALUES (?, ?)", [2, "O'Brien"])
    rows = db.query("SELECT * FROM pyt_users ORDER BY id")
    assert rows.columns == ["id", "name"] and len(rows) == 2
    assert rows.dicts() == [{"id": "1", "name": "ada"}, {"id": "2", "name": "O'Brien"}]
    with pytest.raises(TriCoreError):
        db.query("THIS IS NOT SQL")
    with pytest.raises(TriCoreError):
        db.query("INSERT INTO pyt_users VALUES (3, 'x')")
    assert len(db.query("SELECT id FROM pyt_users").rows) == 2


def test_fragmented_frames_reassemble(db: TriCore) -> None:
    big = bytes(range(256)) * 400
    db.cache_set("pyt", "big", big)
    assert db.cache_get("pyt", "big") == big


def test_llm_exports(db: TriCore, server: Any) -> None:
    db.execute("CREATE TABLE pyt_llm (id INT PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO pyt_llm VALUES (1, 'grace-hopper')")
    assert "pyt_llm" in str(db.llm_schema())
    assert "grace-hopper" in str(db.llm_context([SqlSource("SELECT * FROM pyt_llm")]))
