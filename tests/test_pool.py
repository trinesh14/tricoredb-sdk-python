"""The pool against a real server: exclusivity under contention, bounds, timeout."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from tricoredb import Pool, PoolTimeout

pytestmark = pytest.mark.live

THREADS, PER_THREAD, SIZE = 8, 40, 4


def test_exclusive_ownership_under_contention(server: Any) -> None:
    with Pool(server.host, server.port, user="admin", secret="pw", size=SIZE) as pool:
        with pool.get() as db:
            db.execute("CREATE TABLE pooltest (id INT PRIMARY KEY, v TEXT)")
        assert pool.stats()["created"] == 1
        with pool.get() as db:
            db.ping()
        assert pool.stats()["created"] == 1

        in_flight: set[int] = set()
        guard = threading.Lock()
        errors: list[str] = []

        def worker(w: int) -> None:
            try:
                for i in range(PER_THREAD):
                    with pool.get() as db:
                        with guard:
                            if id(db) in in_flight:
                                raise AssertionError("connection handed to two threads at once")
                            in_flight.add(id(db))
                        db.execute("INSERT INTO pooltest VALUES (?, ?)", [w * 1000 + i, "v"])
                        with guard:
                            in_flight.discard(id(db))
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=worker, args=(w,)) for w in range(THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors[:3]
        assert pool.stats()["created"] <= SIZE
        with pool.get() as db:
            assert len(db.query("SELECT id FROM pooltest").rows) == THREADS * PER_THREAD


def test_exhausted_pool_times_out(server: Any) -> None:
    with Pool(server.host, server.port, user="admin", secret="pw", size=1) as pool:
        with pool.get():
            start = time.monotonic()
            with pytest.raises(PoolTimeout):
                with pool.get(timeout=0.5):
                    pass
            assert time.monotonic() - start < 5
