"""Session transactions against a real server."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tricoredb import FEATURE_SESSION_TXN, FEATURES, Pool, TriCore, TriCoreError

pytestmark = pytest.mark.live

T = "txn_t"
KW = dict(user="admin", secret="pw")


class Boom(RuntimeError):
    pass


def count(db: TriCore) -> int:
    return int(db.query(f"SELECT COUNT(*) FROM {T}").rows[0][0])


@pytest.fixture(scope="module")
def conns(server: Any) -> Iterator[tuple[TriCore, TriCore]]:
    db = TriCore.connect(server.host, server.port, **KW)  # type: ignore[arg-type]
    other = TriCore.connect(server.host, server.port, **KW)  # type: ignore[arg-type]
    db.execute(f"CREATE TABLE {T} (id INT PRIMARY KEY, v INT)")
    yield db, other
    db.close()
    other.close()


def test_a_granted_and_rollback_leaves_no_row(conns: tuple[TriCore, TriCore]) -> None:
    db, _ = conns
    assert db.features.session_txn and db.granted_features & FEATURE_SESSION_TXN
    out = db.begin()
    assert out["transaction"] == "began" and db.in_transaction
    db.execute(f"INSERT INTO {T} VALUES (1, 100)")
    assert count(db) == 1
    out = db.rollback()
    assert out["transaction"] == "rolled_back" and out["discarded_writes"] == 1
    assert not db.in_transaction and count(db) == 0


def test_b_commit_persists_and_is_visible_elsewhere(conns: tuple[TriCore, TriCore]) -> None:
    db, other = conns
    db.begin()
    db.execute(f"INSERT INTO {T} VALUES (?, ?)", [2, 200])
    out = db.commit()
    assert out["transaction"] == "committed" and out["committed_writes"] == 1 and not db.in_transaction
    assert count(db) == 1 and count(other) == 1


def test_c_error_aborts_the_block(conns: tuple[TriCore, TriCore]) -> None:
    db, _ = conns
    db.begin()
    db.execute(f"INSERT INTO {T} VALUES (3, 300)")
    with pytest.raises(TriCoreError):
        db.execute("INSERT INTO no_such_table VALUES (1, 1)")
    with pytest.raises(TriCoreError) as ei:
        db.execute(f"INSERT INTO {T} VALUES (4, 400)")
    assert "aborted" in str(ei.value) and "ROLLBACK" in str(ei.value)
    assert db.in_transaction
    with pytest.raises(TriCoreError) as ei:
        db.commit()
    assert "`COMMIT` is refused" in str(ei.value) and "rolled back" in str(ei.value)
    assert not db.in_transaction and count(db) == 1

    db.begin()
    db.execute(f"INSERT INTO {T} VALUES (3, 300)")
    with pytest.raises(TriCoreError):
        db.execute("INSERT INTO no_such_table VALUES (1, 1)")
    assert db.rollback()["transaction"] == "rolled_back"
    db.execute(f"INSERT INTO {T} VALUES (9, 900)")
    assert count(db) == 2
    db.execute(f"DELETE FROM {T} WHERE id = 9")


def test_d_isolation_and_socket_binding(conns: tuple[TriCore, TriCore]) -> None:
    db, other = conns
    db.begin()
    db.execute(f"INSERT INTO {T} VALUES (5, 500)")
    assert count(db) == 2 and count(other) == 1
    with pytest.raises(TriCoreError) as ei:
        other.commit()
    assert "`COMMIT` is refused" in str(ei.value) and "no transaction is open" in str(ei.value)
    assert count(other) == 1
    db.commit()
    assert count(other) == 2


def test_nesting_is_refused(conns: tuple[TriCore, TriCore]) -> None:
    db, _ = conns
    db.begin()
    db.execute(f"INSERT INTO {T} VALUES (6, 600)")
    with pytest.raises(TriCoreError) as ei:
        db.begin()
    assert "`BEGIN` is refused" in str(ei.value) and "already open" in str(ei.value)
    assert db.in_transaction
    db.rollback()
    assert count(db) == 2


def test_e_transaction_block(conns: tuple[TriCore, TriCore]) -> None:
    db, other = conns
    with pytest.raises(Boom, match="application error after a write"):
        with db.transaction_block() as tx:
            tx.execute(f"INSERT INTO {T} VALUES (7, 700)")
            raise Boom("application error after a write")
    assert not db.in_transaction and count(db) == 2
    with db.transaction_block() as tx:
        tx.execute(f"INSERT INTO {T} VALUES (8, 800)")
    assert count(other) == 3


def test_f_without_session_txn_nothing_autocommits(server: Any, conns: tuple[TriCore, TriCore]) -> None:
    plain = TriCore.connect(server.host, server.port, features=FEATURES & ~FEATURE_SESSION_TXN, **KW)  # type: ignore[arg-type]
    try:
        assert plain.features.session_txn is False and plain.features.server_params is True
        with pytest.raises(TriCoreError) as ei:
            plain.begin()
        assert "did not grant session transactions" in str(ei.value) and "transaction([...])" in str(ei.value)
        assert not plain.in_transaction
        script = plain.transaction([f"INSERT INTO {T} VALUES (10, 1000)", f"DELETE FROM {T} WHERE id = 10"])
        assert script["transaction"] == "committed"
        with pytest.raises(TriCoreError) as ei:
            plain.execute("BEGIN")
        assert "BEGIN; <statements>; COMMIT" in str(ei.value)
        assert count(plain) == 3
    finally:
        plain.close()


def test_pool_never_idles_a_connection_mid_block(server: Any, conns: tuple[TriCore, TriCore]) -> None:
    _, other = conns
    pool = Pool(server.host, server.port, size=2, **KW)  # type: ignore[arg-type]
    try:
        with pytest.raises(TriCoreError) as ei:
            with pool.get() as c:
                c.begin()
                c.execute(f"INSERT INTO {T} VALUES (11, 1100)")
        assert "still open" in str(ei.value) and "rolled back" in str(ei.value)
        assert count(other) == 3

        with pytest.raises(Boom):
            with pool.get() as c:
                c.begin()
                c.execute(f"INSERT INTO {T} VALUES (12, 1200)")
                raise Boom("callback failed mid-block")
        assert count(other) == 3

        with pool.get() as c:
            reused = (c.in_transaction, count(c))
        assert reused == (False, 3) and pool.stats()["created"] == 1

        with pool.get() as c:
            with c.transaction_block() as tx:
                tx.execute(f"INSERT INTO {T} VALUES (13, 1300)")
        assert count(other) == 4 and pool.stats()["idle"] == 1
    finally:
        pool.close()
