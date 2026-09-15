"""The document model: collections, filters, indexes, aggregation."""

from __future__ import annotations

from typing import Any, Optional, Union

from ._base import ClientBase


class Filter:
    """Builders for a document filter.

    ``field`` takes dot notation and a missing path never matches (``ne``
    included). There is no ``or``, ``not`` or regex, because the server has none.
    """

    @staticmethod
    def all() -> Any:
        return "All"

    @staticmethod
    def eq(field: str, value: Any) -> dict[str, Any]:
        return {"Eq": {"field": field, "value": value}}

    @staticmethod
    def ne(field: str, value: Any) -> dict[str, Any]:
        return {"Ne": {"field": field, "value": value}}

    @staticmethod
    def gt(field: str, value: Any) -> dict[str, Any]:
        return {"Gt": {"field": field, "value": value}}

    @staticmethod
    def gte(field: str, value: Any) -> dict[str, Any]:
        return {"Gte": {"field": field, "value": value}}

    @staticmethod
    def lt(field: str, value: Any) -> dict[str, Any]:
        return {"Lt": {"field": field, "value": value}}

    @staticmethod
    def lte(field: str, value: Any) -> dict[str, Any]:
        return {"Lte": {"field": field, "value": value}}

    @staticmethod
    def in_(field: str, values: Any) -> dict[str, Any]:
        return {"In": {"field": field, "values": list(values)}}

    @staticmethod
    def contains(field: str, value: Any) -> dict[str, Any]:
        """Substring of a string field, or membership in an array field."""
        return {"Contains": {"field": field, "value": value}}

    @staticmethod
    def and_(*filters: Any) -> dict[str, Any]:
        """Every sub-filter must match. No arguments matches everything."""
        return {"And": list(filters)}


class GroupBy:
    """The key half of an aggregation ``$group``."""

    @staticmethod
    def field(path: str) -> dict[str, Any]:
        """Group by a path; a document missing it groups under ``null``."""
        return {"Field": path}

    @staticmethod
    def constant(value: Any) -> dict[str, Any]:
        """One group for the whole collection."""
        return {"Constant": value}


class Acc:
    """Accumulators for ``$group``. Documents missing the field are ignored."""

    @staticmethod
    def sum(field: str) -> dict[str, Any]:
        return {"Sum": field}

    @staticmethod
    def avg(field: str) -> dict[str, Any]:
        return {"Avg": field}

    @staticmethod
    def min(field: str) -> dict[str, Any]:
        return {"Min": field}

    @staticmethod
    def max(field: str) -> dict[str, Any]:
        return {"Max": field}

    @staticmethod
    def count() -> Any:
        return "Count"


SortKey = Union[str, "tuple[str, bool]", "dict[str, Any]"]


class Stage:
    """Builders for one aggregation stage. Stages run strictly in order.

    ``$lookup``, ``$unwind``, ``$facet``, ``$out``, ``$addFields`` and computed
    expressions are not implemented by the server and are refused by name.
    """

    @staticmethod
    def match(filter: Any) -> dict[str, Any]:
        return {"Match": filter}

    @staticmethod
    def group(by: Any, accumulators: Optional[list[Any]] = None) -> dict[str, Any]:
        """``by`` is a ``GroupBy``; each accumulator comes from :meth:`Stage.acc`."""
        return {"Group": {"by": by, "accumulators": list(accumulators or [])}}

    @staticmethod
    def acc(output: str, op: Any) -> dict[str, Any]:
        """One accumulator writing into ``output`` (which must not be ``_id``)."""
        return {"output": output, "op": op}

    @staticmethod
    def sort(*keys: SortKey) -> dict[str, Any]:
        """Each key is ``"field"`` (ascending) or ``("field", descending)``."""
        out: list[Any] = []
        for k in keys:
            if isinstance(k, str):
                out.append({"field": k, "descending": False})
            elif isinstance(k, dict):
                out.append(k)
            else:
                name, descending = k
                out.append({"field": name, "descending": bool(descending)})
        return {"Sort": out}

    @staticmethod
    def skip(n: int) -> dict[str, Any]:
        return {"Skip": n}

    @staticmethod
    def limit(n: int) -> dict[str, Any]:
        return {"Limit": n}

    @staticmethod
    def project(fields: Any, include: bool = True) -> dict[str, Any]:
        """Keep or drop top-level fields. Nested projection is refused."""
        return {"Project": {"fields": list(fields), "include": include}}

    @staticmethod
    def count(field: str) -> dict[str, Any]:
        """Replace the stream with one document holding the input count."""
        return {"Count": {"field": field}}


def _document_update(
    set_: Optional[dict[str, Any]], inc: Optional[dict[str, Any]]
) -> dict[str, Any]:
    update: dict[str, Any] = {}
    if set_:
        update["set"] = set_
    if inc:
        update["inc"] = inc
    return update


class DocumentMixin(ClientBase):
    def doc_create_collection(self, collection: str, database: str = "main") -> None:
        self._json({"Document": {"CreateCollection": {"collection": collection}}}, database)

    def doc_drop_collection(self, collection: str, database: str = "main") -> None:
        self._json({"Document": {"DropCollection": {"collection": collection}}}, database)

    def doc_list_collections(self, database: str = "main") -> list[str]:
        return list(self._json({"Document": "ListCollections"}, database).get("collections") or [])

    def doc_insert(
        self,
        collection: str,
        document: dict[str, Any],
        id: Optional[str] = None,
        database: str = "main",
    ) -> str:
        """Insert and return the id (generated when omitted). Not an upsert."""
        body = self._json(
            {"Document": {"Insert": {"collection": collection, "id": id, "document": document}}},
            database,
        )
        return str(body.get("id", ""))

    def doc_get(self, collection: str, id: str, database: str = "main") -> Optional[dict[str, Any]]:
        """The document, or ``None`` when no document has that id."""
        docs = self._documents({"Document": {"Get": {"collection": collection, "id": id}}}, database)
        return docs[0] if docs else None

    def doc_find(
        self,
        collection: str,
        filter: Any = None,
        limit: Optional[int] = None,
        database: str = "main",
    ) -> list[dict[str, Any]]:
        """Documents matching ``filter`` (see :class:`Filter`). ``None`` matches all."""
        return self._documents(
            {"Document": {"Find": {"collection": collection,
                                   "filter": Filter.all() if filter is None else filter,
                                   "limit": limit}}},
            database,
        )

    def doc_aggregate(
        self, collection: str, pipeline: list[Any], database: str = "main"
    ) -> list[dict[str, Any]]:
        """Run an aggregation pipeline (see :class:`Stage`). Read-only."""
        return self._documents(
            {"Document": {"Aggregate": {"collection": collection, "pipeline": pipeline}}},
            database,
        )

    def doc_update(
        self, collection: str, id: str, set: dict[str, Any], database: str = "main"
    ) -> None:
        """Set fields (dot paths allowed) on an existing document. Not an upsert."""
        self._json(
            {"Document": {"Update": {"collection": collection, "id": id, "set": set}}},
            database,
        )

    def doc_update_one(
        self,
        collection: str,
        id: str,
        set: Optional[dict[str, Any]] = None,
        inc: Optional[dict[str, Any]] = None,
        upsert: bool = False,
        database: str = "main",
    ) -> dict[str, Any]:
        """Apply ``set`` then ``inc`` to one document. ``inc`` never coerces."""
        return dict(self._json(
            {"Document": {"UpdateOne": {"collection": collection, "id": id,
                                        "update": _document_update(set, inc),
                                        "upsert": upsert}}},
            database,
        ))

    def doc_update_many(
        self,
        collection: str,
        filter: Any,
        set: Optional[dict[str, Any]] = None,
        inc: Optional[dict[str, Any]] = None,
        database: str = "main",
    ) -> dict[str, Any]:
        """Apply ``set``/``inc`` to every match. Returns ``{"matched", "modified"}``."""
        return dict(self._json(
            {"Document": {"UpdateMany": {"collection": collection, "filter": filter,
                                         "update": _document_update(set, inc)}}},
            database,
        ))

    def doc_delete(self, collection: str, id: str, database: str = "main") -> None:
        self._json({"Document": {"Delete": {"collection": collection, "id": id}}}, database)

    def doc_create_index(
        self,
        collection: str,
        index_name: str,
        field: str,
        unique: bool = False,
        database: str = "main",
    ) -> None:
        """Index a top-level field. Only a top-level ``Filter.eq`` uses an index."""
        self._json(
            {"Document": {"CreateIndex": {"collection": collection, "index_name": index_name,
                                          "field": field, "unique": unique}}},
            database,
        )

    def doc_drop_index(self, collection: str, index_name: str, database: str = "main") -> None:
        self._json(
            {"Document": {"DropIndex": {"collection": collection, "index_name": index_name}}},
            database,
        )

    def doc_list_indexes(self, collection: str, database: str = "main") -> list[dict[str, Any]]:
        body = self._json({"Document": {"ListIndexes": {"collection": collection}}}, database)
        return list(body.get("indexes") or [])

    def doc_analyze(self, collection: str, database: str = "main") -> dict[str, Any]:
        """Approximate statistics the optimizer uses."""
        return dict(self._json({"Document": {"Analyze": {"collection": collection}}}, database))
