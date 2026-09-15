"""The vector model: fixed-dimension collections and similarity search."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Optional

from ._base import ClientBase
from .types import Page, VectorHits


class VectorMixin(ClientBase):
    def vector_create_collection(
        self,
        collection: str,
        dimension: int,
        metric: str = "cosine",
        quantization: Optional[str] = None,
        database: str = "main",
    ) -> dict[str, Any]:
        """``metric`` is ``cosine``, ``dot`` or ``l2``; ``quantization`` is ``none``
        or ``int8``. Both are validated by the server, which refuses by name."""
        op: dict[str, Any] = {"collection": collection, "dimension": dimension, "metric": metric}
        if quantization is not None:
            op["quantization"] = quantization
        return dict(self._json({"Vector": {"CreateCollection": op}}, database))

    def vector_drop_collection(self, collection: str, database: str = "main") -> None:
        self._json({"Vector": {"DropCollection": {"collection": collection}}}, database)

    def vector_list_collections(self, database: str = "main") -> list[str]:
        return list(self._json({"Vector": "ListCollections"}, database).get("collections") or [])

    def vector_describe_collection(self, collection: str, database: str = "main") -> dict[str, Any]:
        """Dimension, metric, quantization and stored count."""
        return dict(self._json({"Vector": {"DescribeCollection": {"collection": collection}}}, database))

    def vector_upsert(
        self,
        collection: str,
        id: str,
        vector: Iterable[float],
        metadata: Any = None,
        database: str = "main",
    ) -> str:
        """Store a vector, replacing any under ``id``. The dimension must match."""
        return str(self._json(
            {"Vector": {"Upsert": {"collection": collection, "id": id,
                                   "vector": [float(x) for x in vector],
                                   "metadata": metadata}}},
            database,
        ).get("id", ""))

    def vector_get(self, collection: str, id: str, database: str = "main") -> Optional[dict[str, Any]]:
        """``{"id", "vector", "metadata"}``, or ``None``. Always full ``f32`` precision."""
        result = self._json({"Vector": {"Get": {"collection": collection, "id": id}}}, database)
        return None if result is None else dict(result)

    def vector_delete(self, collection: str, id: str, database: str = "main") -> None:
        self._json({"Vector": {"Delete": {"collection": collection, "id": id}}}, database)

    def vector_search(
        self,
        collection: str,
        vector: Iterable[float],
        top_k: int = 10,
        filter: Optional[dict[str, Any]] = None,
        database: str = "main",
    ) -> VectorHits:
        """Nearest neighbours, best first. ``filter`` is a flat object of
        top-level metadata field to required value (exact equality only)."""
        return VectorHits(self._json(
            {"Vector": {"Search": {"collection": collection,
                                   "vector": [float(x) for x in vector],
                                   "top_k": top_k, "filter": filter}}},
            database,
        ))

    def vector_list_vectors(
        self,
        collection: str,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        database: str = "main",
    ) -> Page:
        """One page of stored vectors, ordered by id."""
        body = self._json(
            {"Vector": {"ListVectors": {"collection": collection, "limit": limit, "offset": offset}}},
            database,
        )
        return Page(body.get("vectors") or [], body.get("total", 0), bool(body.get("truncated")))
