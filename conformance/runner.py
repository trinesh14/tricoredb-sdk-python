#!/usr/bin/env python3
"""TriCoreDB conformance runner for the `tricoredb` Python package.

    python conformance/runner.py <host> <port> <user> <secret>  < scenario.json

Reads ``{"steps": [...]}`` on stdin and writes exactly one JSON object per step
on stdout. It makes no assertions and never computes or defaults a value: each
action maps to the public SDK method a user would call, and the typed result is
copied into canonical JSON.

The package is imported from this repository's ``src/`` directory. Set
``TRICOREDB_RUNNER_INSTALLED=1`` to import an installed wheel instead.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

if os.environ.get("TRICOREDB_RUNNER_INSTALLED") != "1":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tricoredb import (  # noqa: E402
    Acc, DocumentSource, Filter, GroupBy, SqlSource, Stage, TriCore,
)


def build_filter(spec: dict[str, Any]) -> Any:
    op = spec["op"]
    if op == "all":
        return Filter.all()
    if op == "eq":
        return Filter.eq(spec["field"], spec["value"])
    if op == "gt":
        return Filter.gt(spec["field"], spec["value"])
    if op == "contains":
        return Filter.contains(spec["field"], spec["value"])
    if op == "and":
        return Filter.and_(*[build_filter(f) for f in spec["filters"]])
    raise ValueError(f"unsupported filter op in the scenario: {op}")


def build_acc(a: dict[str, Any]) -> Any:
    if a["op"] == "sum":
        return Stage.acc(a["output"], Acc.sum(a["field"]))
    if a["op"] == "count":
        return Stage.acc(a["output"], Acc.count())
    raise ValueError(f"unsupported accumulator: {a['op']}")


def build_stage(spec: dict[str, Any]) -> Any:
    kind = spec["stage"]
    if kind == "match":
        return Stage.match(build_filter(spec["filter"]))
    if kind == "group":
        return Stage.group(GroupBy.field(spec["by"]["field"]), [build_acc(a) for a in spec["accumulators"]])
    if kind == "sort":
        return Stage.sort(*[(k["field"], k.get("descending", False)) for k in spec["keys"]])
    if kind == "count":
        return Stage.count(spec["field"])
    raise ValueError(f"unsupported aggregate stage: {kind}")


ACTIONS: dict[str, Callable[..., Any]] = {}


def action(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def register(fn: Callable[..., Any]) -> Callable[..., Any]:
        ACTIONS[name] = fn
        return fn
    return register


# ---- document ----

@action("doc.createCollection")
def _doc_create(db, a):
    db.doc_create_collection(a["collection"])
    return {}


@action("doc.dropCollection")
def _doc_drop(db, a):
    db.doc_drop_collection(a["collection"])
    return {}


@action("doc.listCollections")
def _doc_list(db, a):
    return {"names": db.doc_list_collections()}


@action("doc.insert")
def _doc_insert(db, a):
    return {"id": db.doc_insert(a["collection"], a["document"], id=a.get("id"))}


@action("doc.get")
def _doc_get(db, a):
    doc = db.doc_get(a["collection"], a["id"])
    return {"found": False} if doc is None else {"found": True, "doc": doc}


@action("doc.find")
def _doc_find(db, a):
    return {"docs": db.doc_find(a["collection"], build_filter(a["filter"]), limit=a.get("limit"))}


@action("doc.update")
def _doc_update(db, a):
    db.doc_update(a["collection"], a["id"], a["set"])
    return {}


@action("doc.updateOne")
def _doc_update_one(db, a):
    db.doc_update_one(a["collection"], a["id"], set=a.get("set"), inc=a.get("inc"),
                      upsert=bool(a.get("upsert")))
    return {}


@action("doc.updateMany")
def _doc_update_many(db, a):
    r = db.doc_update_many(a["collection"], build_filter(a["filter"]), set=a.get("set"), inc=a.get("inc"))
    return {"matched": r.get("matched"), "modified": r.get("modified")}


@action("doc.delete")
def _doc_delete(db, a):
    db.doc_delete(a["collection"], a["id"])
    return {}


@action("doc.createIndex")
def _doc_create_index(db, a):
    db.doc_create_index(a["collection"], a["indexName"], a["field"], unique=bool(a.get("unique")))
    return {}


@action("doc.dropIndex")
def _doc_drop_index(db, a):
    db.doc_drop_index(a["collection"], a["indexName"])
    return {}


@action("doc.listIndexes")
def _doc_list_indexes(db, a):
    return {"indexes": [{"name": i.get("index_name"), "field": i.get("field")}
                        for i in db.doc_list_indexes(a["collection"])]}


@action("doc.analyze")
def _doc_analyze(db, a):
    return {"document_count": db.doc_analyze(a["collection"]).get("document_count")}


@action("doc.aggregate")
def _doc_aggregate(db, a):
    return {"docs": db.doc_aggregate(a["collection"], [build_stage(s) for s in a["pipeline"]])}


# ---- vector ----

@action("vec.createCollection")
def _vec_create(db, a):
    db.vector_create_collection(a["collection"], a["dimension"], metric=a["metric"])
    return {}


@action("vec.dropCollection")
def _vec_drop(db, a):
    db.vector_drop_collection(a["collection"])
    return {}


@action("vec.listCollections")
def _vec_list(db, a):
    return {"names": db.vector_list_collections()}


@action("vec.upsert")
def _vec_upsert(db, a):
    db.vector_upsert(a["collection"], a["id"], a["vector"], metadata=a.get("metadata"))
    return {}


@action("vec.get")
def _vec_get(db, a):
    v = db.vector_get(a["collection"], a["id"])
    if v is None:
        return {"found": False}
    return {"found": True, "id": v.get("id"), "vector": v.get("vector"), "metadata": v.get("metadata")}


@action("vec.delete")
def _vec_delete(db, a):
    db.vector_delete(a["collection"], a["id"])
    return {}


@action("vec.search")
def _vec_search(db, a):
    hits = db.vector_search(a["collection"], a["vector"], a["topK"], filter=a.get("filter"))
    return {"ids": [m.id for m in hits], "scores": [m.score for m in hits]}


@action("vec.describeCollection")
def _vec_describe(db, a):
    d = db.vector_describe_collection(a["collection"])
    return {"dimension": d.get("dimension"), "metric": d.get("metric"), "count": d.get("count")}


@action("vec.listVectors")
def _vec_list_vectors(db, a):
    page = db.vector_list_vectors(a["collection"])
    return {"ids": [v.get("id") for v in page], "total": page.total}


# ---- graph ----

@action("graph.create")
def _graph_create(db, a):
    db.graph_create(a["graph"])
    return {}


@action("graph.drop")
def _graph_drop(db, a):
    db.graph_drop(a["graph"])
    return {}


@action("graph.listGraphs")
def _graph_list(db, a):
    return {"names": db.graph_list()}


@action("graph.addNode")
def _graph_add_node(db, a):
    db.graph_add_node(a["graph"], a["id"], labels=a.get("labels") or [], properties=a.get("properties") or {})
    return {}


@action("graph.getNode")
def _graph_get_node(db, a):
    n = db.graph_get_node(a["graph"], a["id"])
    if n is None:
        return {"found": False}
    return {"found": True, "id": n.get("id"), "labels": n.get("labels"), "properties": n.get("properties")}


@action("graph.deleteNode")
def _graph_delete_node(db, a):
    db.graph_delete_node(a["graph"], a["id"])
    return {}


@action("graph.addEdge")
def _graph_add_edge(db, a):
    db.graph_add_edge(a["graph"], a["id"], a["from"], a["to"], a["label"], properties=a.get("properties") or {})
    return {}


@action("graph.getEdge")
def _graph_get_edge(db, a):
    e = db.graph_get_edge(a["graph"], a["id"])
    if e is None:
        return {"found": False}
    return {"found": True, "id": e.get("id"), "from": e.get("from"), "to": e.get("to"),
            "label": e.get("label"), "properties": e.get("properties")}


@action("graph.deleteEdge")
def _graph_delete_edge(db, a):
    db.graph_delete_edge(a["graph"], a["id"])
    return {}


@action("graph.neighbors")
def _graph_neighbors(db, a):
    ns = db.graph_neighbors(a["graph"], a["nodeId"], direction=a.get("direction", "outgoing"), label=a.get("label"))
    return {"nodeIds": [n.get("node_id") for n in ns], "edgeIds": [n.get("edge_id") for n in ns]}


@action("graph.degree")
def _graph_degree(db, a):
    return {"degree": db.graph_degree(a["graph"], a["nodeId"], direction=a.get("direction", "outgoing"))}


@action("graph.traverse")
def _graph_traverse(db, a):
    t = db.graph_traverse(a["graph"], a["start"], direction=a.get("direction", "outgoing"),
                          max_depth=a.get("maxDepth"))
    return {"ids": [n.get("id") for n in t.nodes], "depths": {n.get("id"): n.get("depth") for n in t.nodes}}


@action("graph.shortestPath")
def _graph_shortest(db, a):
    p = db.graph_shortest_path(a["graph"], a["from"], a["to"])
    return {"found": p.found, "hops": p.hops, "nodePath": p.node_path, "edgePath": p.edge_path}


@action("graph.weightedShortestPath")
def _graph_weighted(db, a):
    p = db.graph_weighted_shortest_path(a["graph"], a["from"], a["to"], weight_property=a.get("weightProperty"))
    return {"found": p.found, "totalCost": p.total_cost, "nodePath": p.node_path, "edgePath": p.edge_path}


@action("graph.listNodes")
def _graph_list_nodes(db, a):
    page = db.graph_list_nodes(a["graph"])
    return {"ids": [n.get("id") for n in page],
            "labels": {n.get("id"): n.get("labels") for n in page},
            "total": page.total}


@action("graph.listEdges")
def _graph_list_edges(db, a):
    page = db.graph_list_edges(a["graph"])
    return {"ids": [e.get("id") for e in page],
            "labels": {e.get("id"): e.get("label") for e in page},
            "total": page.total}


@action("graph.query")
def _graph_query(db, a):
    q = db.graph_query(a["graph"], a["cypher"])
    return {"columns": q.columns, "rows": q.rows}


# ---- sql ----

@action("sql.execute")
def _sql_execute(db, a):
    resp = db.execute(a["sql"])
    data = resp.data if isinstance(resp.data, dict) else {}
    payload = data.get("Json") if isinstance(data.get("Json"), dict) else {}
    affected = payload.get("rows_affected")
    return {"rowsAffected": affected if isinstance(affected, int) else None}


@action("sql.query")
def _sql_query(db, a):
    rows = db.query(a["sql"])
    return {"columns": rows.columns, "rows": rows.rows}


# ---- cache ----

def _bufs(values):
    return [v.encode("utf-8") for v in (values or [])]


def _pairs(entries):
    return [(f.encode("utf-8"), v.encode("utf-8")) for f, v in (entries or [])]


def _value(v):
    return {"found": False} if v is None else {"found": True, "value": v.decode("utf-8")}


def _entry(e):
    return {"id": e.id, "fields": [[f.decode("utf-8"), v.decode("utf-8")] for f, v in e.fields]}


@action("cache.ping")
def _cache_ping(db, a):
    db.cache_ping()
    return {}


@action("cache.set")
def _cache_set(db, a):
    db.cache_set(a["namespace"], a["key"], a["value"].encode("utf-8"), a.get("ttlMs"))
    return {}


@action("cache.get")
def _cache_get(db, a):
    return _value(db.cache_get(a["namespace"], a["key"]))


@action("cache.delete")
def _cache_delete(db, a):
    return {"deleted": db.cache_delete(a["namespace"], a["key"])}


@action("cache.exists")
def _cache_exists(db, a):
    return {"exists": db.cache_exists(a["namespace"], a["key"])}


@action("cache.ttl")
def _cache_ttl(db, a):
    ttl = db.cache_ttl(a["namespace"], a["key"])
    return {"hasTtl": False} if ttl is None else {"hasTtl": True, "ttlMs": ttl}


@action("cache.clearNamespace")
def _cache_clear(db, a):
    return {"cleared": db.cache_clear_namespace(a["namespace"])}


@action("cache.incr")
def _cache_incr(db, a):
    return {"value": db.cache_incr(a["namespace"], a["key"], a["by"])}


@action("cache.expire")
def _cache_expire(db, a):
    return {"updated": db.cache_expire(a["namespace"], a["key"], a["ttlMs"])}


@action("cache.persist")
def _cache_persist(db, a):
    return {"persisted": db.cache_persist(a["namespace"], a["key"])}


@action("cache.setNx")
def _cache_set_nx(db, a):
    return {"set": db.cache_set_nx(a["namespace"], a["key"], a["value"].encode("utf-8"), a.get("ttlMs"))}


@action("cache.keys")
def _cache_keys(db, a):
    return {"keys": [k["key"] for k in db.cache_keys(a["namespace"], a.get("pattern"))]}


@action("cache.lPush")
def _cache_lpush(db, a):
    return {"length": db.cache_lpush(a["namespace"], a["key"], _bufs(a["values"]))}


@action("cache.rPush")
def _cache_rpush(db, a):
    return {"length": db.cache_rpush(a["namespace"], a["key"], _bufs(a["values"]))}


@action("cache.lPop")
def _cache_lpop(db, a):
    return _value(db.cache_lpop(a["namespace"], a["key"]))


@action("cache.rPop")
def _cache_rpop(db, a):
    return _value(db.cache_rpop(a["namespace"], a["key"]))


@action("cache.lRange")
def _cache_lrange(db, a):
    return {"values": [v.decode("utf-8") for v in db.cache_lrange(a["namespace"], a["key"], a["start"], a["stop"])]}


@action("cache.lLen")
def _cache_llen(db, a):
    return {"length": db.cache_llen(a["namespace"], a["key"])}


@action("cache.lIndex")
def _cache_lindex(db, a):
    return _value(db.cache_lindex(a["namespace"], a["key"], a["index"]))


@action("cache.sAdd")
def _cache_sadd(db, a):
    return {"added": db.cache_sadd(a["namespace"], a["key"], _bufs(a["members"]))}


@action("cache.sRem")
def _cache_srem(db, a):
    return {"removed": db.cache_srem(a["namespace"], a["key"], _bufs(a["members"]))}


@action("cache.sIsMember")
def _cache_sismember(db, a):
    return {"isMember": db.cache_sismember(a["namespace"], a["key"], a["member"].encode("utf-8"))}


@action("cache.sCard")
def _cache_scard(db, a):
    return {"cardinality": db.cache_scard(a["namespace"], a["key"])}


@action("cache.sMembers")
def _cache_smembers(db, a):
    return {"members": [m.decode("utf-8") for m in db.cache_smembers(a["namespace"], a["key"])]}


@action("cache.hSet")
def _cache_hset(db, a):
    return {"created": db.cache_hset(a["namespace"], a["key"], _pairs(a["entries"]))}


@action("cache.hGet")
def _cache_hget(db, a):
    return _value(db.cache_hget(a["namespace"], a["key"], a["field"].encode("utf-8")))


@action("cache.hDel")
def _cache_hdel(db, a):
    return {"deleted": db.cache_hdel(a["namespace"], a["key"], _bufs(a["fields"]))}


@action("cache.hGetAll")
def _cache_hgetall(db, a):
    return {"entries": [[f.decode("utf-8"), v.decode("utf-8")] for f, v in db.cache_hgetall(a["namespace"], a["key"])]}


@action("cache.hExists")
def _cache_hexists(db, a):
    return {"exists": db.cache_hexists(a["namespace"], a["key"], a["field"].encode("utf-8"))}


@action("cache.hLen")
def _cache_hlen(db, a):
    return {"length": db.cache_hlen(a["namespace"], a["key"])}


@action("cache.xAdd")
def _cache_xadd(db, a):
    return {"id": db.cache_xadd(a["namespace"], a["key"], _pairs(a["fields"]))}


@action("cache.xLen")
def _cache_xlen(db, a):
    return {"length": db.cache_xlen(a["namespace"], a["key"])}


@action("cache.xRange")
def _cache_xrange(db, a):
    return {"entries": [_entry(e) for e in db.cache_xrange(a["namespace"], a["key"], a["start"], a["end"])]}


@action("cache.xRead")
def _cache_xread(db, a, ctx):
    return {"entries": [_entry(e) for e in db.cache_xread(a["namespace"], a["key"], ctx[a["afterStep"]]["id"])]}


@action("cache.xDel")
def _cache_xdel(db, a, ctx):
    return {"deleted": db.cache_xdel(a["namespace"], a["key"], [ctx[s]["id"] for s in a["idsFromSteps"]])}


@action("cache.xTrim")
def _cache_xtrim(db, a):
    return {"trimmed": db.cache_xtrim(a["namespace"], a["key"], a["maxLen"])}


@action("cache.xGroup")
def _cache_xgroup(db, a):
    # Consumer groups have no typed helper (the server refuses them by name), so
    # the raw request path is the call a user would make.
    db.request({"Cache": {"XGroup": {"namespace": a["namespace"], "key": a["key"], "command": a["command"]}}})
    return {}


# ---- llm ----

@action("llm.schema")
def _llm_schema(db, a):
    return {"rendered": str(db.llm_schema(output_format=a["format"]))}


@action("llm.context")
def _llm_context(db, a):
    sources = [SqlSource(s["sql"]) if "sql" in s else DocumentSource(s["collection"]) for s in a["sources"]]
    return {"rendered": str(db.llm_context(sources, output_format=a["format"]))}


# ---- admin ----

@action("admin.ping")
def _admin_ping(db, a):
    db.admin_ping()
    return {}


@action("admin.status")
def _admin_status(db, a):
    return {"status": db.admin_status()}


# -- driver ------------------------------------------------------------------

def emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> int:
    if len(sys.argv) < 5:
        sys.stderr.write("usage: runner.py <host> <port> <user> <secret> < scenario.json\n")
        return 2
    host, port, user, secret = sys.argv[1:5]
    scenario = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    steps = scenario["steps"]

    try:
        db = TriCore.connect(host=host, port=int(port), user=user, secret=secret)
    except Exception as e:  # noqa: BLE001 -- reported per step, not judged
        for step in steps:
            emit({"id": step["id"], "status": "error", "error": f"connect failed: {type(e).__name__}: {e}"})
        return 0

    results: dict[str, Any] = {}
    try:
        for step in steps:
            fn = ACTIONS.get(step["action"])
            if fn is None:
                emit({"id": step["id"], "status": "unsupported",
                      "error": f"no Python SDK method for action {step['action']}"})
                continue
            try:
                args = step.get("args") or {}
                value = fn(db, args, results) if fn.__code__.co_argcount >= 3 else fn(db, args)
                results[step["id"]] = value
                emit({"id": step["id"], "status": "ok", "value": value})
            except Exception as e:  # noqa: BLE001 -- the runner reports, it does not judge
                emit({"id": step["id"], "status": "error", "error": f"{type(e).__name__}: {e}"})
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
