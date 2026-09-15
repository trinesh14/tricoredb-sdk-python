"""Parameter conversion and client-side literals, without a server."""

from __future__ import annotations

import datetime
import decimal
import json
import uuid
from typing import Any

import pytest

import tricoredb
from tricoredb import TriCoreError, bind_params, sql_literal

p = tricoredb._sql_param
enc = tricoredb._encode_body


@pytest.mark.parametrize("n", [2 ** 64, 2 ** 80, -(2 ** 80), 9223372036854775807])
def test_wide_integers_pass_through_exactly(n: int) -> None:
    assert p(n) == n
    assert json.dumps({"p": [p(n)]}) == '{"p": [%d]}' % n


@pytest.mark.parametrize(
    "value,want",
    [
        (datetime.datetime(2026, 1, 31, 12, 34, 56), "2026-01-31T12:34:56"),
        (datetime.datetime(2026, 1, 31, 12, 0, 0, tzinfo=datetime.timezone.utc), "2026-01-31T12:00:00+00:00"),
        (datetime.date(2026, 1, 31), "2026-01-31"),
        (datetime.time(12, 34, 56), "12:34:56"),
        (uuid.UUID("550e8400-e29b-41d4-a716-446655440000"), "550e8400-e29b-41d4-a716-446655440000"),
        (b"\xde\xad\xbe\xef", "0xdeadbeef"),
        (bytearray(b"\x00\x01\xff\xfe'\\hi\x00"), "0x0001fffe275c686900"),
    ],
)
def test_json_unserializable_types_become_column_text(value: Any, want: str) -> None:
    assert p(value) == want
    json.dumps(p(value))


def test_decimal_is_sent_as_text_digits() -> None:
    assert enc({"params": [p(decimal.Decimal("0.1"))]}) == b'{"params": ["0.1"]}'
    assert enc({"params": [p(decimal.Decimal("123.456"))]}) == b'{"params": ["123.456"]}'
    several = enc({"params": [p(decimal.Decimal(x)) for x in ("1.5", "2.25", "3")]})
    assert several == b'{"params": ["1.5", "2.25", "3"]}'
    assert enc({"a": 1, "b": [None, False]}) == json.dumps({"a": 1, "b": [None, False]}).encode()


@pytest.mark.parametrize("text", ["0.12345678901234567890123456789", "12345678901234567890.0000000001",
                                  "0.1", "1.5", "123.456", "5", "-0.5", "0.00001", "0.000001", "1.500"])
def test_decimal_precision_is_carried_not_refused(text: str) -> None:
    assert p(decimal.Decimal(text)) == text


def test_decimal_with_exponent_keeps_str_form_on_the_parameter_path() -> None:
    assert p(decimal.Decimal("1.5E+3")) == "1.5E+3"


@pytest.mark.parametrize("bad", ["NaN", "-Infinity", "Infinity", "sNaN"])
def test_non_finite_decimal_parameter_is_refused_by_name(bad: str) -> None:
    with pytest.raises(TriCoreError, match="NaN or Infinity"):
        p(decimal.Decimal(bad))


RAW = b"\x00\x01\xff\xfe'\\hi\x00"


def test_bytes_literal_is_hex_and_never_decoded() -> None:
    with pytest.raises(UnicodeDecodeError):
        RAW.decode("utf-8")
    assert sql_literal(RAW) == "'0x0001fffe275c686900'"
    assert sql_literal(bytearray(RAW)) == "'0x0001fffe275c686900'"
    assert sql_literal(b"") == "'0x'"
    assert bind_params("INSERT INTO t VALUES (?, ?)", [1, RAW]) == "INSERT INTO t VALUES (1, '0x0001fffe275c686900')"
    assert sql_literal(RAW) == tricoredb.quote_sql(p(RAW))


@pytest.mark.parametrize(
    "text,want",
    [("1.5", "1.5"), ("1.500", "1.500"), ("1.5E+3", "1500"), ("1E-6", "0.000001")],
)
def test_decimal_literal_uses_plain_notation(text: str, want: str) -> None:
    assert sql_literal(decimal.Decimal(text)) == want


def test_non_finite_decimal_literal_is_refused_by_name() -> None:
    with pytest.raises(TriCoreError, match="no SQL literal form"):
        sql_literal(decimal.Decimal("NaN"))


def test_other_values_are_unchanged() -> None:
    assert p(True) is True and p(False) is False
    assert p(1) == 1 and isinstance(p(1), int)
    assert p(1.5) == 1.5 and p("x") == "x" and p(None) is None
    sentinel = object()
    assert p(sentinel) is sentinel


def test_bind_params_rules() -> None:
    assert bind_params("SELECT * FROM t WHERE a = ? AND b = '?'", ["O'Brien"]) == \
        "SELECT * FROM t WHERE a = 'O''Brien' AND b = '?'"
    assert bind_params("SELECT ?, ?, ?", [None, True, 2.5]) == "SELECT NULL, TRUE, 2.5"
    with pytest.raises(TriCoreError, match="more `\\?` placeholders"):
        bind_params("SELECT ?, ?", [1])
    with pytest.raises(TriCoreError, match="argument"):
        bind_params("SELECT ?", [1, 2])
    with pytest.raises(TriCoreError, match="unterminated"):
        bind_params("SELECT 'x", [])
    with pytest.raises(TriCoreError, match="no SQL literal form for dict"):
        bind_params("SELECT ?", [{"a": 1}])
    with pytest.raises(TriCoreError, match="no SQL literal form"):
        sql_literal(float("nan"))


def test_transaction_script_rules() -> None:
    from tricoredb.params import transaction_script

    assert transaction_script(["INSERT INTO t VALUES (1);", ("INSERT INTO t VALUES (?)", [b"\x01"])]) == \
        "BEGIN; INSERT INTO t VALUES (1); INSERT INTO t VALUES ('0x01'); COMMIT"
    with pytest.raises(TriCoreError, match="remove the `BEGIN`"):
        transaction_script(["BEGIN"])
    with pytest.raises(TriCoreError, match="non-empty"):
        transaction_script([])
