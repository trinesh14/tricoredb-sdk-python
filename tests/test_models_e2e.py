"""Document, vector and graph APIs, plus SQL parameters, against a real server.

Every read asserts the value that must come back and a value that must not.
"""

from __future__ import annotations

import decimal
from typing import Any, Callable

import pytest

from tricoredb import (
    FEATURE_CORRELATION_ID,
    Acc,
    Filter,
    GroupBy,
    ProtocolError,
    Stage,
    TriCore,
    TriCoreError,
    bind_params,
)

pytestmark = pytest.mark.live


def refused(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> TriCoreError:
    with pytest.raises(TriCoreError) as ei:
        fn(*args, **kwargs)
    return ei.value


def test_documents(db: TriCore) -> None:
    db.doc_create_collection("py_people")
    assert "py_people" in db.doc_list_collections()

    ada = db.doc_insert("py_people", {"name": "ada", "city": "Pune", "age": 36, "tags": ["math", "rust"]}, id="p-ada")
    grace = db.doc_insert("py_people", {"name": "grace", "city": "Oslo", "age": 45, "tags": ["navy"]}, id="p-grace")
    assert (ada, grace) == ("p-ada", "p-grace")
    generated = db.doc_insert("py_people", {"name": "noid", "city": "Oslo", "age": 1})
    assert generated and generated not in ("p-ada", "p-grace")

    got = db.doc_get("py_people", "p-ada")
    assert got is not None
    assert (got["name"], got["city"], got["tags"], got["_id"]) == ("ada", "Pune", ["math", "rust"], "p-ada")
    assert db.doc_get("py_people", "p-nobody") is None

    assert [d["_id"] for d in db.doc_find("py_people", Filter.eq("city", "Pune"))] == ["p-ada"]
    assert [d["_id"] for d in db.doc_find("py_people", Filter.and_(Filter.gt("age", 30), Filter.eq("city", "Oslo")))] == ["p-grace"]
    assert [d["_id"] for d in db.doc_find("py_people", Filter.contains("tags", "rust"))] == ["p-ada"]
    assert [d["_id"] for d in db.doc_find("py_people", Filter.in_("name", ["grace", "absent"]))] == ["p-grace"]
    assert db.doc_find("py_people", Filter.eq("city", "Atlantis")) == []
    assert len(db.doc_find("py_people", limit=2)) == 2

    db.doc_update("py_people", "p-ada", {"city": "Bengaluru", "nested.deep": 7})
    got = db.doc_get("py_people", "p-ada")
    assert got is not None and got["city"] == "Bengaluru" and got["nested"] == {"deep": 7}
    assert db.doc_find("py_people", Filter.eq("city", "Pune")) == []

    res = db.doc_update_one("py_people", "p-ada", set={"city": "Pune"}, inc={"age": 4})
    assert res.get("updated") is True and res.get("inserted") is False
    got = db.doc_get("py_people", "p-ada")
    assert got is not None and (got["city"], got["age"]) == ("Pune", 40)

    res = db.doc_update_one("py_people", "p-new", set={"name": "upserted"}, upsert=True)
    assert res.get("inserted") is True
    new = db.doc_get("py_people", "p-new")
    assert new is not None and new["name"] == "upserted"
    refused(db.doc_update_one, "py_people", "p-ghost", {"x": 1})

    assert db.doc_update_many("py_people", Filter.eq("city", "Oslo"), inc={"age": 1}) == {"matched": 2, "modified": 2}
    assert db.doc_get("py_people", "p-grace")["age"] == 46  # type: ignore[index]
    assert db.doc_get("py_people", "p-ada")["age"] == 40  # type: ignore[index]
    assert db.doc_update_many("py_people", Filter.eq("city", "Atlantis"), inc={"age": 1}) == {"matched": 0, "modified": 0}

    db.doc_create_index("py_people", "by_city", "city")
    assert [i["index_name"] for i in db.doc_list_indexes("py_people")] == ["by_city"]
    assert [d["_id"] for d in db.doc_find("py_people", Filter.eq("city", "Pune"))] == ["p-ada"]
    db.doc_create_index("py_people", "uniq_name", "name", unique=True)
    refused(db.doc_insert, "py_people", {"name": "ada"}, "p-dupe")
    assert db.doc_get("py_people", "p-dupe") is None
    db.doc_drop_index("py_people", "uniq_name")
    assert [i["index_name"] for i in db.doc_list_indexes("py_people")] == ["by_city"]

    stats = db.doc_analyze("py_people")
    assert stats["analyzed"] == "py_people" and stats["document_count"] == 4

    out = db.doc_aggregate("py_people", [
        Stage.match(Filter.eq("city", "Oslo")),
        Stage.group(GroupBy.field("city"), [Stage.acc("total_age", Acc.sum("age")), Stage.acc("n", Acc.count())]),
    ])
    assert out == [{"_id": "Oslo", "total_age": 48, "n": 2}]
    assert db.doc_aggregate("py_people", [Stage.group(GroupBy.constant("all"), [Stage.acc("n", Acc.count())])]) == [{"_id": "all", "n": 4}]
    assert db.doc_aggregate("py_people", [Stage.sort(("age", True)), Stage.limit(1), Stage.project(["name"])]) == [{"name": "grace"}]
    assert db.doc_aggregate("py_people", [Stage.count("n")]) == [{"n": 4}]

    db.doc_delete("py_people", "p-grace")
    assert db.doc_get("py_people", "p-grace") is None and db.doc_get("py_people", "p-ada") is not None

    blob = "".join(chr(33 + (i % 90)) for i in range(100_000))
    db.doc_insert("py_people", {"blob": blob}, id="p-big")
    assert db.doc_get("py_people", "p-big")["blob"] == blob  # type: ignore[index]

    db.doc_drop_collection("py_people")
    assert "py_people" not in db.doc_list_collections()


def test_document_refusals(db: TriCore) -> None:
    refused(db.doc_insert, "py_nope", {"a": 1})
    refused(db.doc_find, "py_nope", Filter.all())
    refused(db.doc_drop_collection, "py_nope")
    db.doc_create_collection("py_neg")
    refused(db.doc_insert, "py_neg", [1, 2, 3])
    db.doc_insert("py_neg", {"a": 1}, id="dup")
    refused(db.doc_insert, "py_neg", {"a": 2}, "dup")
    assert db.doc_get("py_neg", "dup")["a"] == 1  # type: ignore[index]
    db.doc_insert("py_neg", {"s": "text"}, id="str")
    refused(db.doc_update_one, "py_neg", "str", None, {"s": 1})
    assert db.doc_get("py_neg", "str")["s"] == "text"  # type: ignore[index]
    refused(db.doc_aggregate, "py_neg", [{"Lookup": {}}])
    refused(db.doc_find, "py_neg", {"Eq": {"field": "a"}})
    refused(db.request, {"Document": {"Frobnicate": {"collection": "py_neg"}}})
    db.doc_drop_collection("py_neg")


def test_vectors(db: TriCore) -> None:
    db.vector_create_collection("py_vecs", dimension=3, metric="cosine")
    assert "py_vecs" in db.vector_list_collections()
    meta = db.vector_describe_collection("py_vecs")
    assert (meta["dimension"], meta["metric"], meta["count"], meta["quantization"]) == (3, "cosine", 0, "none")

    db.vector_upsert("py_vecs", "x", [1.0, 0.0, 0.0], {"axis": "x", "n": 1})
    db.vector_upsert("py_vecs", "y", [0.0, 1.0, 0.0], {"axis": "y", "n": 2})
    db.vector_upsert("py_vecs", "z", [0.0, 0.0, 1.0], {"axis": "z", "n": 3})

    item = db.vector_get("py_vecs", "y")
    assert item == {"id": "y", "vector": [0.0, 1.0, 0.0], "metadata": {"axis": "y", "n": 2}} or (
        item is not None and item["id"] == "y" and item["vector"] == [0.0, 1.0, 0.0]
        and item["metadata"] == {"axis": "y", "n": 2})
    assert db.vector_get("py_vecs", "absent") is None

    hits = db.vector_search("py_vecs", [0.9, 0.1, 0.0], top_k=3)
    assert hits.index == "flat" and hits.ids()[0] == "x" and hits.ids()[-1] == "z"
    assert hits[0].score > hits[1].score > hits[2].score
    assert hits[0].metadata == {"axis": "x", "n": 1}
    assert db.vector_search("py_vecs", [0.0, 0.0, 1.0], top_k=1).ids() == ["z"]
    assert db.vector_search("py_vecs", [1.0, 1.0, 1.0], top_k=3, filter={"axis": "y"}).ids() == ["y"]
    assert db.vector_search("py_vecs", [1.0, 0.0, 0.0], top_k=3, filter={"axis": "nothing"}).ids() == []

    db.vector_upsert("py_vecs", "y", [0.0, 2.0, 0.0], {"axis": "y", "n": 22})
    item = db.vector_get("py_vecs", "y")
    assert item is not None and item["vector"] == [0.0, 2.0, 0.0] and item["metadata"]["n"] == 22
    assert db.vector_describe_collection("py_vecs")["count"] == 3

    page = db.vector_list_vectors("py_vecs")
    assert [v["id"] for v in page] == ["x", "y", "z"] and page.total == 3 and page.truncated is False
    page = db.vector_list_vectors("py_vecs", limit=2)
    assert [v["id"] for v in page] == ["x", "y"] and page.truncated is True and page.total == 3
    page = db.vector_list_vectors("py_vecs", limit=2, offset=2)
    assert [v["id"] for v in page] == ["z"] and page.truncated is False

    db.vector_delete("py_vecs", "y")
    assert db.vector_get("py_vecs", "y") is None
    assert sorted(db.vector_search("py_vecs", [0.0, 1.0, 0.0], top_k=3).ids()) == ["x", "z"]

    db.vector_create_collection("py_l2", dimension=2, metric="l2")
    db.vector_upsert("py_l2", "near", [0.0, 0.0])
    db.vector_upsert("py_l2", "far", [10.0, 10.0])
    hits = db.vector_search("py_l2", [0.1, 0.1], top_k=2)
    assert hits.ids() == ["near", "far"] and hits[0].score > hits[1].score
    assert hits[0].score <= 0.0 and hits[1].score < -100

    db.vector_create_collection("py_q", dimension=2, metric="dot", quantization="int8")
    assert db.vector_describe_collection("py_q")["quantization"] == "int8"
    db.vector_upsert("py_q", "a", [0.25, 0.5])
    assert db.vector_get("py_q", "a")["vector"] == [0.25, 0.5]  # type: ignore[index]

    db.vector_create_collection("py_ann", dimension=8, metric="cosine")
    for i in range(300):
        vec = [0.0] * 8
        vec[i % 8] = 1.0 + (i / 1000.0)
        db.vector_upsert("py_ann", f"v{i:03d}", vec, {"bucket": i % 8})
    planted = [0.0] * 8
    planted[3] = 1.0
    db.vector_upsert("py_ann", "planted", planted, {"bucket": "planted"})
    hits = db.vector_search("py_ann", planted, top_k=1)
    assert hits.index.startswith("hnsw") and hits.ids() == ["planted"]
    assert hits[0].metadata == {"bucket": "planted"}

    names = ("py_vecs", "py_l2", "py_q", "py_ann")
    for name in names:
        db.vector_drop_collection(name)
    assert not set(names) & set(db.vector_list_collections())


def test_vector_refusals(db: TriCore) -> None:
    refused(db.vector_upsert, "py_gone", "a", [1.0])
    refused(db.vector_describe_collection, "py_gone")
    db.vector_create_collection("py_vneg", dimension=3, metric="cosine")
    refused(db.vector_upsert, "py_vneg", "a", [1.0, 2.0])
    assert db.vector_get("py_vneg", "a") is None
    db.vector_upsert("py_vneg", "a", [1.0, 2.0, 3.0])
    refused(db.vector_search, "py_vneg", [1.0, 2.0], 1)
    refused(db.vector_search, "py_vneg", [1.0, 2.0, 3.0], 0)
    refused(db.vector_search, "py_vneg", [1.0, 2.0, 3.0], 100_000)
    refused(db.vector_search, "py_vneg", [1.0, 2.0, 3.0], 1, ["axis"])
    refused(db.vector_upsert, "py_vneg", "", [1.0, 2.0, 3.0])
    refused(db.vector_create_collection, "py_vneg", 3, "cosine")
    refused(db.vector_create_collection, "py_vneg2", 3, "manhattan")
    refused(db.vector_create_collection, "py_vneg3", 0, "cosine")
    refused(db.request, {"Vector": {"Frobnicate": {"collection": "py_vneg"}}})
    db.vector_drop_collection("py_vneg")


def test_graphs(db: TriCore) -> None:
    G = "py_social"
    db.graph_create(G)
    assert G in db.graph_list()
    for nid, city in (("a", "Pune"), ("b", "Oslo"), ("c", "Lima"), ("lonely", "Nowhere")):
        db.graph_add_node(G, nid, labels=["Person"], properties={"city": city})
    db.graph_add_node(G, "srv", labels=["Server"], properties={"city": "Pune"})

    node = db.graph_get_node(G, "b")
    assert node is not None and (node["id"], node["labels"], node["properties"]) == ("b", ["Person"], {"city": "Oslo"})
    assert db.graph_get_node(G, "ghost") is None

    db.graph_add_edge(G, "e-ab", "a", "b", "KNOWS", {"weight": 1})
    db.graph_add_edge(G, "e-bc", "b", "c", "KNOWS", {"weight": 1})
    db.graph_add_edge(G, "e-ac", "a", "c", "KNOWS", {"weight": 5})
    db.graph_add_edge(G, "e-asrv", "a", "srv", "USES", {"weight": 1})
    edge = db.graph_get_edge(G, "e-ab")
    assert edge is not None and (edge["from"], edge["to"], edge["label"], edge["properties"]) == ("a", "b", "KNOWS", {"weight": 1})
    assert db.graph_get_edge(G, "e-ghost") is None

    assert sorted(n["node_id"] for n in db.graph_neighbors(G, "a")) == ["b", "c", "srv"]
    assert [n["node_id"] for n in db.graph_neighbors(G, "a", label="USES")] == ["srv"]
    assert sorted(n["node_id"] for n in db.graph_neighbors(G, "c", direction="incoming")) == ["a", "b"]
    assert db.graph_neighbors(G, "c", direction="outgoing") == []
    assert sorted((n["node_id"], n["direction"]) for n in db.graph_neighbors(G, "b", direction="both")) == \
        [("a", "incoming"), ("c", "outgoing")]

    assert db.graph_degree(G, "a") == 3
    assert db.graph_degree(G, "a", direction="incoming") == 0
    assert db.graph_degree(G, "b", direction="both") == 2
    assert db.graph_degree(G, "lonely", direction="both") == 0

    walk = db.graph_traverse(G, "a", max_depth=1)
    assert sorted(walk.ids()) == ["a", "b", "c", "srv"] and walk.max_depth == 1 and walk.truncated is False
    depths = {n["id"]: n["depth"] for n in walk}
    assert depths["a"] == 0 and depths["b"] == 1
    walk = db.graph_traverse(G, "a", limit=2)
    assert len(walk) == 2 and walk.truncated is True
    assert db.graph_traverse(G, "a", max_depth=99).max_depth == 10
    assert db.graph_traverse(G, "lonely").ids() == ["lonely"]

    path = db.graph_shortest_path(G, "a", "c")
    assert path.found and path.node_path == ["a", "c"] and path.edge_path == ["e-ac"] and path.hops == 1
    assert path.total_cost is None
    weighted = db.graph_weighted_shortest_path(G, "a", "c")
    assert weighted.found and weighted.node_path == ["a", "b", "c"]
    assert weighted.edge_path == ["e-ab", "e-bc"] and weighted.total_cost == 2.0
    miss = db.graph_shortest_path(G, "a", "lonely")
    assert miss.found is False and not miss and miss.node_path == [] and "no path" in miss.message

    rows = db.graph_query(G, "MATCH (n:Person) RETURN n.city ORDER BY n.city")
    assert rows.columns == ["n.city"] and [r[0] for r in rows] == ["Lima", "Nowhere", "Oslo", "Pune"]
    assert rows.truncated is False
    assert [r[0] for r in db.graph_query(G, "MATCH (n:Person) WHERE n.city = 'Oslo' RETURN n.city")] == ["Oslo"]
    assert db.graph_query(G, "MATCH (n:Person) RETURN count(n)").rows == [[4]]
    limited = db.graph_query(G, "MATCH (n:Person) RETURN n.city LIMIT 1")
    assert len(limited) == 1 and limited.truncated is True

    page = db.graph_list_nodes(G)
    assert [n["id"] for n in page] == ["a", "b", "c", "lonely", "srv"] and page.total == 5 and not page.truncated
    page = db.graph_list_nodes(G, limit=2, offset=1)
    assert [n["id"] for n in page] == ["b", "c"] and page.truncated is True and page.total == 5
    page = db.graph_list_edges(G)
    assert [e["id"] for e in page] == ["e-ab", "e-ac", "e-asrv", "e-bc"] and page.total == 4

    db.graph_delete_edge(G, "e-ac")
    assert db.graph_get_edge(G, "e-ac") is None and db.graph_degree(G, "a") == 2
    assert db.graph_shortest_path(G, "a", "c").node_path == ["a", "b", "c"]
    db.graph_delete_node(G, "srv")
    assert db.graph_get_node(G, "srv") is None and db.graph_get_node(G, "a") is not None
    db.graph_drop(G)
    assert G not in db.graph_list()


def test_graph_refusals(db: TriCore) -> None:
    refused(db.graph_add_node, "py_gone", "a")
    refused(db.graph_neighbors, "py_gone", "a")
    refused(db.graph_drop, "py_gone")
    db.graph_create("py_gneg")
    refused(db.graph_create, "py_gneg")
    refused(db.graph_add_node, "py_gneg", "")
    db.graph_add_node("py_gneg", "a")
    refused(db.graph_add_edge, "py_gneg", "e1", "a", "ghost", "L")
    refused(db.graph_add_edge, "py_gneg", "e2", "ghost", "a", "L")
    assert db.graph_get_edge("py_gneg", "e1") is None
    refused(db.graph_traverse, "py_gneg", "ghost")
    refused(db.graph_shortest_path, "py_gneg", "ghost", "a")
    refused(db.graph_neighbors, "py_gneg", "a", "sideways")
    db.graph_add_node("py_gneg", "b")
    db.graph_add_edge("py_gneg", "neg", "a", "b", "L", {"weight": -3})
    refused(db.graph_weighted_shortest_path, "py_gneg", "a", "b")
    refused(db.graph_query, "py_gneg", "CREATE (n:X) RETURN n")
    refused(db.graph_query, "py_gneg", "MATCH (n) WITH n RETURN n")
    refused(db.graph_query, "py_gneg", "THIS IS NOT CYPHER")
    refused(db.request, {"Graph": {"Frobnicate": {"graph": "py_gneg"}}})
    db.graph_drop("py_gneg")


def test_wrong_payload_shape_raises(db: TriCore) -> None:
    with pytest.raises(ProtocolError, match="expected Json"):
        db._json({"Sql": {"Query": {"sql": "SELECT 1"}}}, "main")


BLOB = b"\x00\x01\xff\xfe'\\hi\x00"


def test_sql_params_round_trip(db: TriCore) -> None:
    db.execute("CREATE TABLE pyparams (id INT PRIMARY KEY, b BLOB, d DECIMAL)")
    db.execute("INSERT INTO pyparams (id, b) VALUES (?, ?)", [1, BLOB])
    assert db.query("SELECT b FROM pyparams WHERE id = ?", [1]).rows[0][0] == "0x0001fffe275c686900"
    db.execute("INSERT INTO pyparams (id, b) VALUES (?, ?)", [2, bytearray(b"\xde\xad\xbe\xef")])
    assert db.query("SELECT b FROM pyparams WHERE id = 2").rows[0][0] == "0xdeadbeef"
    db.execute("INSERT INTO pyparams (id, b) VALUES (?, ?)", [3, b""])
    assert db.query("SELECT b FROM pyparams WHERE id = 3").rows[0][0] == "0x"

    cases = [
        "1.5", "123.456", "0.1", "5",
        "0.123456789012345678",
        "12345678901234567890.123456789012345678",
        "0.000001",
        "1.50",
    ]
    for i, sent in enumerate(cases, start=10):
        db.execute("INSERT INTO pyparams (id, d) VALUES (?, ?)", [i, decimal.Decimal(sent)])
        back = db.query(f"SELECT d FROM pyparams WHERE id = {i}").rows[0][0]
        assert back == sent, (sent, back)

    db.execute("INSERT INTO pyparams (id, d) VALUES (?, ?)", [30, decimal.Decimal("1.5E+3")])
    assert decimal.Decimal(db.query("SELECT d FROM pyparams WHERE id = 30").rows[0][0]) == decimal.Decimal("1500")

    assert float(db.query("SELECT d FROM pyparams WHERE id = 12").rows[0][0]) == 0.1
    assert len(db.query("SELECT id FROM pyparams WHERE d = ?", [decimal.Decimal("1.5")]).rows) == 2

    refused(db.execute, "INSERT INTO pyparams (id, d) VALUES (?, ?)", [99, decimal.Decimal("0.12345678901234567890123456789")])
    e = refused(db.execute, "INSERT INTO pyparams (id, d) VALUES (?, ?)", [98, decimal.Decimal("NaN")])
    assert "NaN" in str(e)
    assert db.query("SELECT id FROM pyparams WHERE id = 99 OR id = 98").rows == []


def test_without_server_params_nothing_falls_back(server: Any) -> None:
    with TriCore.connect(server.host, server.port, user="admin", secret="pw",
                         features=FEATURE_CORRELATION_ID) as db:
        assert db.granted_features == FEATURE_CORRELATION_ID
        db.execute("CREATE TABLE pylegacy (id INT PRIMARY KEY, b BLOB, d DECIMAL)")

        e = refused(db.execute, "INSERT INTO pylegacy (id, b) VALUES (?, ?)", [1, BLOB])
        assert "SERVER_PARAMS" in str(e) and e.not_sent
        refused(db.query, "SELECT id FROM pylegacy WHERE id = ?", [1])
        assert db.query("SELECT id FROM pylegacy").rows == [], "the refused insert must not have been sent"

        db.execute(bind_params("INSERT INTO pylegacy (id, b) VALUES (?, ?)", [21, BLOB]))
        literal_path = db.query("SELECT b FROM pylegacy WHERE id = 21").rows[0][0]
        assert literal_path == "0x" + BLOB.hex()
        db.execute(bind_params("INSERT INTO pylegacy (id, b) VALUES (?, ?)", [22, bytearray(b"")]))
        assert db.query("SELECT b FROM pylegacy WHERE id = 22").rows[0][0] == "0x"

        for i, (sent, want) in enumerate([("1.5", "1.5"), ("1E-6", "0.000001"), ("1.50", "1.50")], start=23):
            db.execute(bind_params("INSERT INTO pylegacy (id, d) VALUES (?, ?)", [i, decimal.Decimal(sent)]))
            assert db.query(f"SELECT d FROM pylegacy WHERE id = {i}").rows[0][0] == want

        with pytest.raises(TriCoreError, match="no SQL literal form"):
            bind_params("INSERT INTO pylegacy (id, d) VALUES (?, ?)", [26, decimal.Decimal("NaN")])

        out = db.transaction([("INSERT INTO pylegacy (id, d) VALUES (?, ?)", [30, decimal.Decimal("2.25")])])
        assert out["transaction"] == "committed"
        assert db.query("SELECT d FROM pylegacy WHERE id = 30").rows[0][0] == "2.25"

    with TriCore.connect(server.host, server.port, user="admin", secret="pw") as full:
        full.execute("INSERT INTO pylegacy (id, b) VALUES (?, ?)", [1, BLOB])
        assert full.query("SELECT b FROM pylegacy WHERE id = 1").rows[0][0] == literal_path
