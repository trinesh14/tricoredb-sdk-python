"""SQL values: server-side parameters and client-side literals.

Two paths, and they must store the same bytes:

* :func:`sql_param` converts one value into the JSON scalar sent in a request's
  ``params`` array (the server binds it; requires ``SERVER_PARAMS``).
* :func:`sql_literal` / :func:`bind_params` render values into SQL text. Used
  when the caller renders explicitly, and by ``transaction([...])``, whose
  one-request script is a single SQL string.
"""

from __future__ import annotations

import datetime as _dt
import decimal as _decimal
import uuid as _uuid
from collections.abc import Sequence
from typing import Any, Optional, Union

from .errors import TriCoreError

Statement = Union[str, "tuple[str, Optional[Sequence[Any]]]"]


def sql_param(value: Any) -> Any:
    """One Python value as a server-side parameter (a JSON scalar).

    ``datetime``/``date``/``time`` and ``UUID`` become text, ``bytes`` and
    ``bytearray`` become ``0x`` + hex, and ``Decimal`` becomes its own digits as
    text. Integers pass through (JSON writes them exactly). Anything else passes
    through untouched so an unsupported type reaches a real refusal instead of
    being stringified.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, _uuid.UUID):
        return str(value)
    if isinstance(value, _decimal.Decimal):
        return decimal_param(value)
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    return value


def decimal_param(value: _decimal.Decimal) -> str:
    """A ``Decimal`` as the text a ``DECIMAL`` column parses exactly.

    Not a float and not a JSON number: the server holds a JSON number as an
    ``f64``, which loses digits. Text goes through the server's exact decimal
    parser, which also reads an exponent without a float, so ``str`` is safe
    here. NaN and the infinities are refused by name because JSON cannot carry
    them. Precision and scale limits are left to the server, which refuses by
    parameter index.
    """
    if not value.is_finite():
        raise TriCoreError(
            f"`{value}` cannot be a SQL parameter: JSON has no NaN or Infinity, and no "
            "TriCoreDB column holds one"
        )
    return str(value)


def bind_params(sql: str, args: Sequence[Any]) -> str:
    """Render ``?`` placeholders into SQL text, client-side.

    Strings are escaped by doubling ``'`` (the only escape the tokenizer knows);
    ``bytes`` render as the quoted ``'0x..'`` a BLOB column parses, whatever the
    bytes are; ``Decimal`` renders in plain notation; only a closed set of types
    is accepted; placeholder and argument counts must match; a ``?`` inside a
    string literal is left alone. Identifiers cannot be bound.

    >>> bind_params("SELECT * FROM t WHERE city = ? AND age > ?", ["Pune", 30])
    "SELECT * FROM t WHERE city = 'Pune' AND age > 30"
    """
    if not isinstance(sql, str):
        raise TriCoreError("sql must be a string")
    values = list(args)
    out: list[str] = []
    nxt = 0
    in_string = False
    i = 0
    while i < len(sql):
        c = sql[i]
        if c == "'":
            if in_string and i + 1 < len(sql) and sql[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_string = not in_string
            out.append(c)
            i += 1
            continue
        if c == "?" and not in_string:
            if nxt >= len(values):
                raise TriCoreError(
                    f"SQL has more `?` placeholders than the {len(values)} argument(s) supplied"
                )
            out.append(sql_literal(values[nxt]))
            nxt += 1
            i += 1
            continue
        out.append(c)
        i += 1

    if in_string:
        raise TriCoreError("SQL ends inside an unterminated string literal")
    if nxt != len(values):
        raise TriCoreError(
            f"SQL has {nxt} `?` placeholder(s) but {len(values)} argument(s) were supplied"
        )
    return "".join(out)


def sql_literal(value: Any) -> str:
    """One Python value as a SQL literal."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise TriCoreError(f"`{value}` has no SQL literal form")
        return repr(value)
    if isinstance(value, _decimal.Decimal):
        # `str` may keep an exponent, and the tokenizer reads any literal with an
        # exponent as DOUBLE. Plain notation lexes as an exact DECIMAL.
        if not value.is_finite():
            raise TriCoreError(f"`{value}` has no SQL literal form")
        return format(value, "f")
    if isinstance(value, str):
        return quote_sql(value)
    if isinstance(value, (bytes, bytearray)):
        # Never decoded as UTF-8: the same `0x` hex the parameter path sends.
        return quote_sql("0x" + bytes(value).hex())
    raise TriCoreError(
        f"no SQL literal form for {type(value).__name__}. Convert it explicitly: falling back "
        "to repr() would put an object's debug rendering into the statement."
    )


def quote_sql(s: str) -> str:
    """Escape and quote a string: double every ``'``."""
    return "'" + s.replace("'", "''") + "'"


def bind(sql: str, args: Optional[Sequence[Any]]) -> str:
    return sql if args is None else bind_params(sql, args)


def transaction_script(statements: Sequence[Any]) -> str:
    """Assemble ``BEGIN; ...; COMMIT``. Caller-supplied transaction control is refused."""
    items = list(statements)
    if not items:
        raise TriCoreError("transaction() needs a non-empty sequence of statements")
    parts = []
    for s in items:
        if isinstance(s, str):
            sql, args = s, None
        elif isinstance(s, (tuple, list)) and len(s) == 2:
            sql, args = s[0], s[1]
        else:
            raise TriCoreError("each transaction statement must be `sql` or `(sql, args)`")
        if not isinstance(sql, str) or not sql.strip():
            raise TriCoreError("each transaction statement must be a non-empty SQL string")
        text = bind(sql, args).strip().rstrip(";").strip()
        first = text.split()[0].upper()
        if first in ("BEGIN", "START", "COMMIT", "ROLLBACK"):
            raise TriCoreError(
                f"transaction() brackets the script itself; remove the `{first}` statement"
            )
        parts.append(text)
    return "BEGIN; " + "; ".join(parts) + "; COMMIT"
