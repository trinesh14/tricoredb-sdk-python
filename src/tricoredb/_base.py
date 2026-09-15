"""The request funnel shared by every data-model mixin."""

from __future__ import annotations

from typing import Any, Optional

from .errors import ProtocolError
from .types import Response, kind


class ClientBase:
    """Payload unwrapping on top of :meth:`request`.

    A payload arm the caller did not ask for raises ``ProtocolError`` rather
    than reading as an empty result.
    """

    def request(self, op: dict[str, Any], database: str = "main") -> Response:
        raise NotImplementedError

    def _json(self, op: dict[str, Any], database: str) -> Any:
        data = self.request(op, database).data
        if isinstance(data, dict) and "Json" in data:
            return data["Json"]
        raise ProtocolError(f"expected Json, got {kind(data)}")

    def _documents(self, op: dict[str, Any], database: str) -> list[Any]:
        data = self.request(op, database).data
        if isinstance(data, dict) and "Documents" in data:
            return list(data["Documents"] or [])
        raise ProtocolError(f"expected Documents, got {kind(data)}")

    def _cache_value(self, op: dict[str, Any], database: str) -> Optional[bytes]:
        """``CacheValue``: bytes, or ``None`` on a miss (presence, not truthiness)."""
        data = self.request(op, database).data
        if isinstance(data, dict) and "CacheValue" in data:
            v = data["CacheValue"]
            return None if v is None else bytes(v)
        raise ProtocolError(f"expected CacheValue, got {kind(data)}")
