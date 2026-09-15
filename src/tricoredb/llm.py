"""Read-only LLM context assembly and schema export."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Optional, Union

from ._base import ClientBase
from .document import Filter
from .errors import ProtocolError, TriCoreError
from .types import Response, kind


@dataclass(frozen=True)
class SqlSource:
    """A SQL ``SELECT`` contributing to a context bundle."""

    query: str

    def wire(self) -> dict[str, Any]:
        return {"Sql": {"query": self.query}}


@dataclass(frozen=True)
class DocumentSource:
    """A document find contributing to a context bundle. ``filter=None`` matches all."""

    collection: str
    filter: Any = None
    limit: Optional[int] = None

    def wire(self) -> dict[str, Any]:
        return {"DocumentFind": {
            "collection": self.collection,
            "filter": Filter.all() if self.filter is None else self.filter,
            "limit": self.limit,
        }}


LlmSource = Union[SqlSource, DocumentSource]


def _rendered(resp: Response) -> Any:
    data = resp.data
    if isinstance(data, dict):
        for arm in ("Toon", "Json", "Message"):
            if arm in data:
                return data[arm]
    raise ProtocolError(f"expected a rendered export, got {kind(data)}")


def _options(max_rows: Optional[int], redact_sensitive: bool, include_schema: bool) -> dict[str, Any]:
    return {"max_rows": max_rows, "redact_sensitive": redact_sensitive,
            "include_schema": include_schema}


class LlmMixin(ClientBase):
    def llm_context(
        self,
        sources: Sequence[LlmSource],
        output_format: str = "toon",
        max_rows: Optional[int] = None,
        redact_sensitive: bool = True,
        include_schema: bool = False,
        database: str = "main",
    ) -> Any:
        """Assemble a bundle from read-only sources. Text for ``toon``/``markdown``,
        a JSON value for ``json``."""
        wire = [s.wire() for s in sources]
        if not wire:
            raise TriCoreError("a context bundle needs at least one source")
        resp = self.request(
            {"Llm": {"Context": {
                "sources": wire,
                "format": output_format,
                "options": _options(max_rows, redact_sensitive, include_schema),
            }}},
            database,
        )
        return _rendered(resp)

    def llm_schema(
        self,
        output_format: str = "toon",
        max_rows: Optional[int] = None,
        redact_sensitive: bool = True,
        include_schema: bool = False,
        database: str = "main",
    ) -> Any:
        """Export the schema catalog: SQL tables plus document collections."""
        resp = self.request(
            {"Llm": {"Schema": {
                "format": output_format,
                "options": _options(max_rows, redact_sensitive, include_schema),
            }}},
            database,
        )
        return _rendered(resp)
