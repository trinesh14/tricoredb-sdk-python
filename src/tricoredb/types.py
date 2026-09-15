"""Typed results: responses, rows, pages, vector hits, graph walks, stream entries."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Optional

from .errors import NOT_LEADER


class Response:
    """A server response: typed data plus how it was produced."""

    __slots__ = ("request_id", "status", "data", "diagnostics")

    def __init__(self, raw: dict[str, Any]) -> None:
        self.request_id: str = raw.get("request_id", "")
        self.status: str = raw.get("status", "error")
        self.data: Any = raw.get("data")
        self.diagnostics: dict[str, Any] = raw.get("diagnostics") or {}

    @property
    def warnings(self) -> list[str]:
        """Non-fatal warnings. A partially applied broadcast DDL reports here
        while still returning ``ok``."""
        return list(self.diagnostics.get("warnings") or [])

    @property
    def route(self) -> str:
        return str(self.diagnostics.get("route", ""))

    @property
    def elapsed_ms(self) -> int:
        return int(self.diagnostics.get("elapsed_ms", 0))

    @property
    def error_code(self) -> Optional[str]:
        """The machine-readable failure reason, or ``None``."""
        return self.diagnostics.get("error_code") or None

    @property
    def leader_hint(self) -> Optional[str]:
        """The leader's ``host:port`` alongside ``not_leader``, or ``None``."""
        return self.diagnostics.get("leader_hint") or None

    @property
    def is_redirect(self) -> bool:
        return self.error_code == NOT_LEADER

    def __repr__(self) -> str:
        return f"Response(status={self.status!r}, route={self.route!r})"


class Rows:
    """A SQL result set. SQL cells arrive as strings."""

    __slots__ = ("columns", "rows")

    def __init__(self, columns: list[str], rows: list[list[Any]]) -> None:
        self.columns = columns
        self.rows = rows

    def dicts(self) -> list[dict[str, Any]]:
        """Rows as dicts keyed by column name."""
        return [dict(zip(self.columns, r)) for r in self.rows]

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self) -> Iterator[list[Any]]:
        return iter(self.rows)

    def __repr__(self) -> str:
        return f"Rows(columns={self.columns!r}, rows={len(self.rows)})"


class CypherRows(Rows):
    """The result of ``graph_query``: decoded JSON cells plus ``truncated``.

    ``truncated`` means a ``LIMIT`` or a resource cap stopped the query before
    every match was returned.
    """

    __slots__ = ("truncated",)

    def __init__(self, raw: dict[str, Any]) -> None:
        super().__init__(raw.get("columns") or [], raw.get("rows") or [])
        self.truncated: bool = bool(raw.get("truncated"))

    def __repr__(self) -> str:
        return (
            f"CypherRows(columns={self.columns!r}, rows={len(self.rows)}, "
            f"truncated={self.truncated})"
        )


class Page:
    """One page of a server-capped listing. The server clamps ``limit``, so
    paging is driven by ``truncated`` and ``total``, not the requested size."""

    __slots__ = ("items", "total", "truncated")

    def __init__(self, items: list[Any], total: int, truncated: bool) -> None:
        self.items = items
        self.total = total
        self.truncated = truncated

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[Any]:
        return iter(self.items)

    def __getitem__(self, i: int) -> Any:
        return self.items[i]

    def __repr__(self) -> str:
        return f"Page(items={len(self.items)}, total={self.total}, truncated={self.truncated})"


class VectorMatch:
    """One ``vector_search`` hit. Higher is always closer, whatever the metric:
    ``l2`` scores are negated squared distances, so they are ``<= 0``."""

    __slots__ = ("id", "score", "metadata")

    def __init__(self, raw: dict[str, Any]) -> None:
        self.id: str = raw.get("id", "")
        self.score: float = raw.get("score", 0.0)
        self.metadata: Any = raw.get("metadata")

    def __repr__(self) -> str:
        return f"VectorMatch(id={self.id!r}, score={self.score!r})"


class VectorHits:
    """``vector_search`` results, best first, plus the index that served them
    (``"flat"``, ``"hnsw"`` or ``"hnsw-int8"``)."""

    __slots__ = ("matches", "index")

    def __init__(self, raw: dict[str, Any]) -> None:
        self.matches = [VectorMatch(h) for h in (raw.get("results") or [])]
        self.index: str = raw.get("index", "")

    def ids(self) -> list[str]:
        return [m.id for m in self.matches]

    def __len__(self) -> int:
        return len(self.matches)

    def __iter__(self) -> Iterator[VectorMatch]:
        return iter(self.matches)

    def __getitem__(self, i: int) -> VectorMatch:
        return self.matches[i]

    def __repr__(self) -> str:
        return f"VectorHits(matches={len(self.matches)}, index={self.index!r})"


class Traversal:
    """The result of ``graph_traverse``. ``max_depth`` and ``limit`` are the
    values the server used after clamping."""

    __slots__ = ("nodes", "truncated", "max_depth", "limit", "start", "direction")

    def __init__(self, raw: dict[str, Any]) -> None:
        self.nodes: list[dict[str, Any]] = raw.get("nodes") or []
        self.truncated: bool = bool(raw.get("truncated"))
        self.max_depth: int = raw.get("max_depth", 0)
        self.limit: int = raw.get("limit", 0)
        self.start: str = raw.get("start", "")
        self.direction: str = raw.get("direction", "")

    def ids(self) -> list[str]:
        return [n.get("id", "") for n in self.nodes]

    def __len__(self) -> int:
        return len(self.nodes)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.nodes)

    def __repr__(self) -> str:
        return f"Traversal(nodes={len(self.nodes)}, truncated={self.truncated})"


class GraphPath:
    """A shortest-path answer. "No path" is ``found = False`` with a reason in
    ``message``, not an error. ``total_cost`` is ``None`` for the unweighted search."""

    __slots__ = ("found", "node_path", "edge_path", "hops", "total_cost", "message", "direction")

    def __init__(self, raw: dict[str, Any]) -> None:
        self.found: bool = bool(raw.get("found"))
        self.node_path: list[str] = raw.get("node_path") or []
        self.edge_path: list[str] = raw.get("edge_path") or []
        self.hops: int = raw.get("hops", 0)
        self.total_cost: Optional[float] = raw.get("total_cost")
        self.message: str = raw.get("message", "")
        self.direction: str = raw.get("direction", "")

    def __bool__(self) -> bool:
        return self.found

    def __repr__(self) -> str:
        return (
            f"GraphPath(found={self.found}, hops={self.hops}, "
            f"node_path={self.node_path!r}, total_cost={self.total_cost!r})"
        )


@dataclass(frozen=True)
class StreamEntry:
    """One cache stream entry. ``fields`` is authoritative (arbitrary bytes);
    ``text`` is a convenience for all-UTF-8 fields."""

    id: str
    fields: list[tuple[bytes, bytes]] = field(default_factory=list)

    @property
    def text(self) -> dict[str, str]:
        return {f.decode("utf-8"): v.decode("utf-8") for f, v in self.fields}


def stream_entries(payload: dict[str, Any]) -> list[StreamEntry]:
    return [
        StreamEntry(e["id"], [(bytes(f), bytes(v)) for f, v in e.get("fields", [])])
        for e in payload.get("entries", [])
    ]


def kind(data: Any) -> str:
    """The payload arm name, for "expected X, got Y" messages."""
    if isinstance(data, dict) and data:
        return str(next(iter(data)))
    return repr(data)
