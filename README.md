# tricoredb

Official Python client for [TriCoreDB](https://hub.docker.com/r/trinesh14/tricoredb) —
SQL, documents, vectors, graphs and cache over one native connection.

[![PyPI](https://img.shields.io/pypi/v/tricoredb.svg?cacheSeconds=3600)](https://pypi.org/project/tricoredb/)
[![Python](https://img.shields.io/pypi/pyversions/tricoredb.svg?cacheSeconds=86400)](https://pypi.org/project/tricoredb/)
[![License](https://img.shields.io/pypi/l/tricoredb.svg?cacheSeconds=86400)](https://github.com/trinesh14/tricoredb-sdk-python/blob/main/LICENSE)

- **No dependencies** — only the Python standard library
- **Fully typed** — ships `py.typed`, checked with `mypy --strict`
- **Server-side parameters** — values never become part of the SQL text
- **Exact values** — `Decimal`, `datetime`, `UUID` and binary data convert exactly
- **Transactions, connection pooling, TLS and mutual TLS**

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Running a server](#running-a-server)
- [Quick start](#quick-start)
- [Connecting](#connecting)
- [SQL](#sql)
- [Transactions](#transactions)
- [Connection pool](#connection-pool)
- [Documents](#documents)
- [Vectors](#vectors)
- [Graphs](#graphs)
- [Cache](#cache)
- [LLM context](#llm-context)
- [Admin](#admin)
- [Errors](#errors)
- [Timeouts and cancellation](#timeouts-and-cancellation)
- [TLS](#tls)
- [Security](#security)
- [Compatibility](#compatibility)

## Requirements

- Python **3.9** or later
- A TriCoreDB server speaking protocol 1.0 (`tricore-server` 0.1.0-rc.1 or later) —
  see [Running a server](#running-a-server)

## Installation

```bash
pip install tricoredb
```

## Running a server

The quickest way is the official Docker image,
[`trinesh14/tricoredb`](https://hub.docker.com/r/trinesh14/tricoredb).

**Local development** (no TLS, no encryption — this machine only). Set
`TRICORE_ADMIN_PASSWORD` in your shell first, then create the admin and start
the server:

```bash
docker run --rm -v tricoredb-dev:/var/lib/tricoredb -e TRICORE_ADMIN_PASSWORD --entrypoint /usr/local/bin/tricore trinesh14/tricoredb:0.1.0-rc.1-r2 auth init-admin --user admin --password-env TRICORE_ADMIN_PASSWORD --data-dir /var/lib/tricoredb/data
docker run -d --name tricoredb-dev -p 127.0.0.1:8427:8427 -e TRICORE_TLS=off -e TRICORE_ENCRYPTION=off -e TRICORE_MODULES=all -v tricoredb-dev:/var/lib/tricoredb trinesh14/tricoredb:0.1.0-rc.1-r2
```

**Anything else:** the image's default is **TLS on** and an **encrypted data
volume**. Follow the quick start on the
[Docker Hub page](https://hub.docker.com/r/trinesh14/tricoredb) to create the
certificate and key, then connect with [TLS](#tls).

`TRICORE_MODULES=all` enables every data model. The image's default is `sql`,
`document` and `cache`; a call to a disabled model fails with an error whose
`code` is `engine.disabled`.

## Quick start

```python
import os
from tricoredb import TriCore

with TriCore.connect("127.0.0.1", 8427, user="admin", secret=os.environ["TRICORE_ADMIN_PASSWORD"]) as db:
    db.execute("CREATE TABLE IF NOT EXISTS users (id INT PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO users VALUES (?, ?)", [1, "Ada"])

    rows = db.query("SELECT id, name FROM users WHERE id = ?", [1])
    print(rows.dicts())   # [{'id': '1', 'name': 'Ada'}]
```

## Connecting

```python
db = TriCore.connect(host, port, user=..., secret=...)
```

| Argument | Default | Description |
| --- | --- | --- |
| `host` | `"127.0.0.1"` | Server host |
| `port` | `8427` | Server port |
| `user` | `None` | Username. Omit to skip authentication. |
| `secret` | `None` | Password |
| `client_name` | `"tricoredb-python"` | Name reported to the server |
| `timeout` | `10.0` | Seconds for connect, TLS, handshake and authentication |
| `tls` | `None` | A `TlsOptions`; see [TLS](#tls) |
| `read_timeout` | `None` | Socket deadline for each reply, in seconds |
| `features` | `FEATURES` | Capabilities to request during the handshake |

A connection is a context manager; `close()` it yourself otherwise.

```python
db.ping()            # transport round trip
db.features          # Features(server_params=..., session_txn=..., correlation_id=...)
db.close()
```

A connection is **not thread-safe**: it is one request/response stream. Use one
connection per thread, or a [`Pool`](#connection-pool).

Every data method takes an optional keyword argument `database`, which defaults
to `"main"`.

## SQL

```python
rows = db.query("SELECT id, name FROM users WHERE name = ?", ["Ada"])
rows.columns     # ['id', 'name']
rows.rows        # [['1', 'Ada']]
rows.dicts()     # [{'id': '1', 'name': 'Ada'}]

resp = db.execute("UPDATE users SET name = ? WHERE id = ?", ["Grace", 1])
resp.warnings    # non-fatal server warnings
```

- `query(sql, args=None)` returns `Rows` and may only run `SELECT`. **Every cell
  is a string** — convert to the type you need.
- `execute(sql, args=None)` returns a `Response`, for writes and DDL.

### Parameters

Use `?` placeholders. `query()` and `execute()` send values **separately** from
the SQL text and the server binds them, so a value can never change what the
statement means. If the server did not grant server-side parameters, the call
fails with a clear error before anything is sent.

| Python value | Sent as | Use for |
| --- | --- | --- |
| `None` | `NULL` | |
| `bool` | boolean | `BOOL` |
| `int` | integer, exactly | `INT`, `BIGINT` |
| `float` | number | `DOUBLE` |
| `str` | text | `TEXT` |
| `Decimal` | its exact digits, as text a `DECIMAL` column parses exactly | `DECIMAL` |
| `datetime`, `date`, `time` | ISO-8601 text | `TIMESTAMP` and friends |
| `UUID` | text | |
| `bytes`, `bytearray` | `0x…` hex | `BLOB` |

- A `Decimal` never passes through a `float`, so no digit is lost. `NaN` and
  `Infinity` are refused by name; precision and scale limits are checked by the
  server.
- Other types are sent unchanged, so an unsupported value gets a clear refusal
  from the server rather than being silently turned into text.
- Identifiers (table and column names) cannot be parameters — validate them
  against an allow-list.

## Transactions

Two shapes:

```python
# 1. Every statement known up front: one request, one round trip.
db.transaction([
    ("INSERT INTO accounts VALUES (?, ?)", [1, 100]),
    ("INSERT INTO accounts VALUES (?, ?)", [2, 50]),
])

# 2. A later statement depends on an earlier read: a session block on this connection.
with db.transaction_block() as tx:
    balance = int(tx.query("SELECT balance FROM accounts WHERE id = ?", [1]).rows[0][0])
    tx.execute("UPDATE accounts SET balance = ? WHERE id = ?", [balance - 10, 1])
# committed here; an exception inside rolls back and re-raises
```

- `transaction(statements)` runs a list of `sql` or `(sql, args)` entries as one
  `BEGIN … COMMIT` script. Its arguments are rendered into the script on the
  client with `bind_params`. It works on every node.
- `begin()`, `commit()`, `rollback()` open and close a block bound to **this**
  connection; `transaction_block()` wraps them. Use the connection it yields
  (`tx`) for every statement inside.
- If the server did not grant session transactions, `begin()` fails by name and
  sends nothing — it never silently autocommits.
- `db.in_transaction` is true while a block is open.

A schema change (for example `CREATE TABLE`) that commits while a block is open
aborts that block; roll back and retry.

## Connection pool

```python
from tricoredb import Pool

with Pool("127.0.0.1", 8427, user="admin", secret=secret, size=8) as pool:
    with pool.get() as db:
        db.execute("INSERT INTO users VALUES (?, ?)", [2, "Grace"])
```

- `Pool(host, port, user=None, secret=None, size=8, timeout=10.0, tls=None, read_timeout=None)`
  opens connections lazily, never more than `size`.
- `pool.get(timeout=10.0)` borrows a connection for the block, or raises
  `PoolTimeout`.
- A connection never returns to the pool with a transaction open: it is rolled
  back, and `get()` raises to say so.

## Documents

```python
from tricoredb import Filter, Stage, GroupBy, Acc

db.doc_create_collection("people")
db.doc_insert("people", {"name": "Ada", "city": "London", "visits": 3}, id="p1")
db.doc_get("people", "p1")                        # dict, or None
db.doc_find("people", Filter.and_(Filter.gt("visits", 1), Filter.eq("city", "London")))
db.doc_update_one("people", "p1", set={"city": "Paris"}, inc={"visits": 1})
db.doc_aggregate("people", [
    Stage.match(Filter.eq("city", "Paris")),
    Stage.group(GroupBy.field("city"), [Stage.acc("n", Acc.count())]),
    Stage.sort(("n", True)),
])
```

Filters: `all`, `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in_`, `contains`, `and_`.
Stages: `match`, `group`, `sort`, `skip`, `limit`, `project`, `count`.

Also: `doc_update`, `doc_update_many`, `doc_delete`, `doc_list_collections`,
`doc_drop_collection`, `doc_create_index`, `doc_drop_index`, `doc_list_indexes`,
`doc_analyze`.

## Vectors

```python
db.vector_create_collection("docs", dimension=3, metric="cosine")   # "cosine" | "dot" | "l2"
db.vector_upsert("docs", "a", [1.0, 0.0, 0.0], {"kind": "note"})
hits = db.vector_search("docs", [0.9, 0.1, 0.0], top_k=5, filter={"kind": "note"})
hits.ids()                                                          # ['a'], best first
```

Higher scores are closer for every metric (`l2` scores are negated distances).

Also: `vector_get`, `vector_delete`, `vector_list_collections`,
`vector_describe_collection`, `vector_list_vectors`, `vector_drop_collection`.

## Graphs

```python
db.graph_create("social")
db.graph_add_node("social", "ada", labels=["Person"], properties={"name": "Ada"})
db.graph_add_node("social", "grace", labels=["Person"])
db.graph_add_edge("social", "e1", "ada", "grace", "KNOWS", {"since": 1843})

db.graph_neighbors("social", "ada", direction="outgoing")
db.graph_shortest_path("social", "ada", "grace").node_path          # ['ada', 'grace']
db.graph_query("social", "MATCH (p:Person) RETURN p.name").rows
```

"No path" is not an error: `graph_shortest_path` returns a `GraphPath` with
`found=False`.

Also: `graph_get_node`, `graph_get_edge`, `graph_delete_node`, `graph_delete_edge`,
`graph_degree`, `graph_traverse`, `graph_weighted_shortest_path`,
`graph_list_nodes`, `graph_list_edges`, `graph_list`, `graph_drop`.

## Cache

Values are `bytes`; `*_text` helpers exist for text.

```python
db.cache_set("sessions", "user:1", b'{"id": 1}', ttl_ms=60_000)
db.cache_get("sessions", "user:1")          # bytes, or None
db.cache_get_text("sessions", "user:1")     # str, or None
db.cache_incr("counters", "page:home")      # new value
db.cache_delete("sessions", "user:1")       # True if it existed
```

| Group | Methods |
| --- | --- |
| Keys | `cache_set`, `cache_set_nx`, `cache_get`, `cache_get_text`, `cache_delete`, `cache_exists`, `cache_ttl`, `cache_expire`, `cache_persist`, `cache_incr`, `cache_keys`, `cache_clear_namespace` |
| Lists | `cache_lpush`, `cache_rpush`, `cache_lpop`, `cache_rpop`, `cache_lrange`, `cache_llen`, `cache_lindex` |
| Sets | `cache_sadd`, `cache_srem`, `cache_sismember`, `cache_scard`, `cache_smembers` |
| Hashes | `cache_hset`, `cache_hset_text`, `cache_hget`, `cache_hdel`, `cache_hgetall`, `cache_hexists`, `cache_hlen` |
| Streams | `cache_xadd`, `cache_xadd_text`, `cache_xlen`, `cache_xrange`, `cache_xread`, `cache_xdel`, `cache_xtrim` |

## LLM context

Build compact, read-only context for a language model from your data:

```python
from tricoredb import SqlSource, DocumentSource

context = db.llm_context(
    [SqlSource("SELECT * FROM orders LIMIT 20"), DocumentSource("people", limit=10)],
    output_format="json",      # "toon" | "markdown" | "json"
)
schema = db.llm_schema(output_format="toon")
```

## Admin

```python
db.admin_ping()      # needs the Admin permission and the cluster module
db.admin_status()    # dict
```

## Errors

Everything raised derives from `TriCoreError`:

| Class | When |
| --- | --- |
| `TriCoreError` | The server refused a request, or a general failure |
| `AuthError` | Authentication failed |
| `ProtocolError` | The server sent something the driver cannot interpret |
| `ConnectionFailed` | Could not connect, or the TLS handshake failed (also an `OSError`) |
| `Timeout` | A deadline elapsed (also an `OSError`) |
| `PoolTimeout` | No pooled connection became free in time |

Branch on `e.code`, never on the message text:

```python
from tricoredb import TriCoreError

try:
    db.execute("INSERT INTO users VALUES (?, ?)", [1, "Ada"])
except TriCoreError as e:
    if e.code == "state.conflict":
        ...  # a concurrent transaction won — retry
    else:
        raise
```

| Code | Meaning |
| --- | --- |
| `perm.denied` | Not authorized for this operation or database |
| `request.invalid` | The server refused the request (bad SQL, type mismatch, …) |
| `request.malformed` | The request could not be interpreted |
| `engine.disabled` | That data model is disabled on the server |
| `limit.exceeded` | A resource limit was reached |
| `state.conflict` | Transaction conflict — retrying the transaction may succeed |
| `not_leader` | This cluster node cannot serve the request (see below) |
| `internal` | Server fault |

`code` is `None` when the server sent none, or when the driver raised the error
itself before sending.

### Cluster redirects

A write that reaches a follower in a cluster fails with `e.code == NOT_LEADER`
and `e.is_redirect` true. `e.leader_hint` is the leader's `host:port`, or `None`
during an election. The driver does **not** follow the hint for you: connect to
the leader yourself — with your own credentials and TLS settings — and decide
whether the write is safe to repeat. A redirect inside an open transaction block
ends that block; nothing is re-sent.

## Timeouts and cancellation

```python
db = TriCore.connect(host, port, user=user, secret=secret, timeout=10.0, read_timeout=30.0)
db.request_timeout_ms = 5_000     # ask the server to stop any request after 5 s

# From a second connection, stop a statement the first one is running:
other.cancel(db.last_request_id)
```

A connection whose read deadline expired is unusable afterwards; open a new one.

## TLS

```python
from tricoredb import TriCore, TlsOptions

tls = TlsOptions(ca_file="tricoredb-ca.crt", server_name="localhost")
db = TriCore.connect("localhost", 8427, user="admin", secret=secret, tls=tls)
```

| Option | Description |
| --- | --- |
| `ca_file` | PEM CA bundle to trust |
| `server_name` | Name expected in the server certificate (SNI); default `"localhost"` |
| `client_cert_file`, `client_key_file` | Client certificate and key, for mutual TLS (set both) |
| `danger_accept_invalid_certs` | Skips certificate verification. **Development only.** |

TLS is off unless you pass `tls`. With no `ca_file` the trust store is **empty**:
the driver never falls back to operating-system root certificates, so a wrong path
fails instead of trusting an unrelated CA.

## Security

- Load credentials from the environment or a secret manager — never commit them.
- Use TLS whenever traffic leaves a trusted network.
- Always pass user input as parameters, never by string formatting.
- Connect with a least-privilege user.

To report a vulnerability, use
[private vulnerability reporting](https://github.com/trinesh14/tricoredb-sdk-python/security/advisories/new)
on GitHub rather than a public issue.

## Compatibility

| tricoredb | Python | TriCoreDB protocol |
| --- | --- | --- |
| 0.1.x | 3.9 – 3.14 | 1.0 |

This package follows [semantic versioning](https://semver.org). See the
[changelog](https://github.com/trinesh14/tricoredb-sdk-python/blob/main/CHANGELOG.md)
for release notes.

## License

[Apache-2.0](https://github.com/trinesh14/tricoredb-sdk-python/blob/main/LICENSE)
