"""The public surface, packaging facts and local refusals. No server needed."""

from __future__ import annotations

import errno
import re
from pathlib import Path

import pytest

import tricoredb
from tricoredb import (
    FEATURE_SERVER_PARAMS,
    FEATURE_SESSION_TXN,
    NOT_LEADER,
    ConnectionFailed,
    DocumentSource,
    Features,
    SqlSource,
    Timeout,
    TriCore,
    TriCoreError,
)

MONOREPO_ALL = [
    "TriCore", "Pool", "Rows", "Response", "TlsOptions", "Features",
    "Filter", "Stage", "Acc", "GroupBy",
    "CypherRows", "Page", "GraphPath", "Traversal", "VectorHits", "VectorMatch",
    "StreamEntry", "SqlSource", "DocumentSource",
    "bind_params", "sql_literal", "quote_sql",
    "TriCoreError", "AuthError", "ProtocolError", "PoolTimeout", "ConnectionFailed",
    "Timeout", "NOT_LEADER",
]

REPO = Path(__file__).resolve().parents[1]


def test_all_matches_the_monorepo_driver() -> None:
    assert sorted(tricoredb.__all__) == sorted(MONOREPO_ALL)
    for name in MONOREPO_ALL:
        assert hasattr(tricoredb, name), name


def test_module_level_constants_still_importable() -> None:
    from tricoredb import (  # noqa: F401
        DEFAULT_PORT, FEATURE_CORRELATION_ID, FEATURES, MAX_CONTROL_FRAME_SIZE, MAX_FRAME_SIZE,
        PROTOCOL, PROTOCOL_VERSION, VERSION,
    )

    assert (DEFAULT_PORT, PROTOCOL, VERSION, PROTOCOL_VERSION) == (8427, "tricore", 1, (1, 0))
    assert (MAX_FRAME_SIZE, MAX_CONTROL_FRAME_SIZE) == (16 * 1024 * 1024, 64 * 1024)
    assert FEATURES == FEATURE_CORRELATION_ID | FEATURE_SERVER_PARAMS | FEATURE_SESSION_TXN


def test_version_matches_pyproject() -> None:
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version = "([^"]+)"', text, re.M)
    assert m is not None
    assert tricoredb.__version__ == m.group(1) == "0.1.0"


def test_py_typed_marker_ships_in_the_package() -> None:
    assert (Path(tricoredb.__file__).parent / "py.typed").is_file()


def test_error_keeps_positional_args_and_keywords() -> None:
    e = TriCoreError("a", "b", code=NOT_LEADER, leader_hint="10.0.0.1:8427")
    assert e.args == ("a", "b")
    assert (e.code, e.leader_hint, e.is_redirect) == (NOT_LEADER, "10.0.0.1:8427", True)
    plain = TriCoreError("x")
    assert (plain.code, plain.leader_hint, plain.is_redirect) == (None, None, False)


@pytest.mark.parametrize("cls", [Timeout, ConnectionFailed])
def test_oserror_subclasses_accept_the_errno_form(cls: type[TriCoreError]) -> None:
    e = cls(errno.ETIMEDOUT, "timed out")
    assert isinstance(e, OSError) and isinstance(e, TriCoreError)
    assert e.errno == errno.ETIMEDOUT  # type: ignore[attr-defined]
    assert e.strerror == "timed out"  # type: ignore[attr-defined]
    coded = cls("msg", code="io")
    assert coded.code == "io" and str(coded) == "msg"


def test_features_by_name() -> None:
    f = Features(FEATURE_SERVER_PARAMS)
    assert (f.server_params, f.session_txn, f.correlation_id) == (True, False, False)


def test_document_source_without_a_filter_matches_all() -> None:
    # The monorepo driver referenced an undefined `DocFilter` here and raised NameError.
    assert DocumentSource("people").wire() == {
        "DocumentFind": {"collection": "people", "filter": "All", "limit": None}
    }
    assert SqlSource("SELECT 1").wire() == {"Sql": {"query": "SELECT 1"}}


def test_bound_params_without_server_params_fail_by_name_before_sending() -> None:
    db = TriCore(None)
    db.granted_features = FEATURES_WITHOUT(FEATURE_SERVER_PARAMS)
    with pytest.raises(TriCoreError) as ei:
        db.execute("INSERT INTO t VALUES (?)", [1])
    assert "SERVER_PARAMS" in str(ei.value) and ei.value.not_sent
    with pytest.raises(TriCoreError) as ei:
        db.query("SELECT * FROM t WHERE id = ?", [1])
    assert "SERVER_PARAMS" in str(ei.value)
    assert db.last_request_id is None


def test_begin_without_session_txn_fails_by_name_before_sending() -> None:
    db = TriCore(None)
    db.granted_features = FEATURES_WITHOUT(FEATURE_SESSION_TXN)
    with pytest.raises(TriCoreError) as ei:
        db.begin()
    assert "SESSION_TXN" in str(ei.value) and "transaction([...])" in str(ei.value)
    assert db.last_request_id is None and not db.in_transaction


def FEATURES_WITHOUT(bit: int) -> int:
    return tricoredb.FEATURES & ~bit
