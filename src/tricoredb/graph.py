"""The graph model: nodes, edges, traversal, paths and a read-only Cypher subset."""

from __future__ import annotations

from typing import Any, Optional

from ._base import ClientBase
from .types import CypherRows, GraphPath, Page, Traversal


class GraphMixin(ClientBase):
    def graph_create(self, graph: str, database: str = "main") -> None:
        self._json({"Graph": {"CreateGraph": {"graph": graph}}}, database)

    def graph_drop(self, graph: str, database: str = "main") -> None:
        self._json({"Graph": {"DropGraph": {"graph": graph}}}, database)

    def graph_list(self, database: str = "main") -> list[str]:
        return list(self._json({"Graph": "ListGraphs"}, database).get("graphs") or [])

    def graph_add_node(
        self,
        graph: str,
        id: str,
        labels: Optional[list[str]] = None,
        properties: Any = None,
        database: str = "main",
    ) -> str:
        return str(self._json(
            {"Graph": {"AddNode": {"graph": graph, "id": id, "labels": list(labels or []),
                                   "properties": properties}}},
            database,
        ).get("id", ""))

    def graph_get_node(self, graph: str, id: str, database: str = "main") -> Optional[dict[str, Any]]:
        """``{"id", "labels", "properties"}``, or ``None``."""
        result = self._json({"Graph": {"GetNode": {"graph": graph, "id": id}}}, database)
        return None if result is None else dict(result)

    def graph_delete_node(self, graph: str, id: str, database: str = "main") -> None:
        self._json({"Graph": {"DeleteNode": {"graph": graph, "id": id}}}, database)

    def graph_add_edge(
        self,
        graph: str,
        id: str,
        from_: str,
        to: str,
        label: str,
        properties: Any = None,
        database: str = "main",
    ) -> str:
        """Both endpoints must exist. ``from_`` is the wire's ``from``."""
        return str(self._json(
            {"Graph": {"AddEdge": {"graph": graph, "id": id, "from": from_, "to": to,
                                   "label": label, "properties": properties}}},
            database,
        ).get("id", ""))

    def graph_get_edge(self, graph: str, id: str, database: str = "main") -> Optional[dict[str, Any]]:
        """``{"id", "from", "to", "label", "properties"}``, or ``None``."""
        result = self._json({"Graph": {"GetEdge": {"graph": graph, "id": id}}}, database)
        return None if result is None else dict(result)

    def graph_delete_edge(self, graph: str, id: str, database: str = "main") -> None:
        self._json({"Graph": {"DeleteEdge": {"graph": graph, "id": id}}}, database)

    def graph_neighbors(
        self,
        graph: str,
        node_id: str,
        direction: str = "outgoing",
        label: Optional[str] = None,
        limit: Optional[int] = None,
        database: str = "main",
    ) -> list[dict[str, Any]]:
        """One entry per incident edge: ``{"edge_id", "node_id", "label", "direction"}``."""
        return list(self._json(
            {"Graph": {"Neighbors": {"graph": graph, "node_id": node_id,
                                     "direction": direction, "label": label, "limit": limit}}},
            database,
        ).get("neighbors") or [])

    def graph_degree(
        self, graph: str, node_id: str, direction: str = "outgoing", database: str = "main"
    ) -> int:
        """Edges incident to a node. ``"both"`` counts each edge once."""
        return int(self._json(
            {"Graph": {"Degree": {"graph": graph, "node_id": node_id, "direction": direction}}},
            database,
        ).get("degree", 0))

    def graph_traverse(
        self,
        graph: str,
        start: str,
        direction: str = "outgoing",
        label: Optional[str] = None,
        max_depth: Optional[int] = None,
        limit: Optional[int] = None,
        database: str = "main",
    ) -> Traversal:
        """Bounded BFS. The server clamps ``max_depth`` (to 10) and ``limit`` (to 1000)."""
        return Traversal(self._json(
            {"Graph": {"Traverse": {"graph": graph, "start": start, "direction": direction,
                                    "label": label, "max_depth": max_depth, "limit": limit}}},
            database,
        ))

    def graph_shortest_path(
        self,
        graph: str,
        from_: str,
        to: str,
        direction: str = "outgoing",
        label: Optional[str] = None,
        max_depth: Optional[int] = None,
        database: str = "main",
    ) -> GraphPath:
        """Fewest-hops path. No path is ``found = False``, not an error."""
        return GraphPath(self._json(
            {"Graph": {"ShortestPath": {"graph": graph, "from": from_, "to": to,
                                        "direction": direction, "label": label,
                                        "max_depth": max_depth}}},
            database,
        ))

    def graph_weighted_shortest_path(
        self,
        graph: str,
        from_: str,
        to: str,
        direction: str = "outgoing",
        label: Optional[str] = None,
        weight_property: Optional[str] = None,
        database: str = "main",
    ) -> GraphPath:
        """Least-cost path (Dijkstra). ``weight_property`` defaults to ``weight``;
        a missing or non-numeric weight counts as 1.0; negative weights are refused."""
        return GraphPath(self._json(
            {"Graph": {"WeightedShortestPath": {"graph": graph, "from": from_, "to": to,
                                                "direction": direction, "label": label,
                                                "weight_property": weight_property}}},
            database,
        ))

    def graph_list_nodes(
        self,
        graph: str,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        database: str = "main",
    ) -> Page:
        body = self._json(
            {"Graph": {"ListNodes": {"graph": graph, "limit": limit, "offset": offset}}}, database
        )
        return Page(body.get("nodes") or [], body.get("total", 0), bool(body.get("truncated")))

    def graph_list_edges(
        self,
        graph: str,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        database: str = "main",
    ) -> Page:
        body = self._json(
            {"Graph": {"ListEdges": {"graph": graph, "limit": limit, "offset": offset}}}, database
        )
        return Page(body.get("edges") or [], body.get("total", 0), bool(body.get("truncated")))

    def graph_query(self, graph: str, cypher: str, database: str = "main") -> CypherRows:
        """Run a read-only Cypher-subset query. Write clauses and unsupported
        clauses are refused by name."""
        return CypherRows(self._json({"Graph": {"Query": {"graph": graph, "cypher": cypher}}}, database))
