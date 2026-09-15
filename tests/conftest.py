from __future__ import annotations

from collections.abc import Iterator

import pytest
from _server import ServerProcess, find_server_binary

from tricoredb import TriCore

USER = "admin"
SECRET = "pw"


@pytest.fixture(scope="session")
def server_binary() -> str:
    binary = find_server_binary()
    if binary is None:
        pytest.skip("no tricore-server binary found; set TRICORE_SERVER_BIN to run live tests")
    return binary


@pytest.fixture(scope="session")
def server(server_binary: str) -> Iterator[ServerProcess]:
    with ServerProcess(server_binary, "sdk-python-tests") as s:
        yield s


@pytest.fixture
def db(server: ServerProcess) -> Iterator[TriCore]:
    conn = TriCore.connect(server.host, server.port, user=USER, secret=SECRET)
    try:
        yield conn
    finally:
        conn.close()
