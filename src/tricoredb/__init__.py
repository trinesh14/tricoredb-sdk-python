"""TriCoreDB client for Python, speaking the native ``tricore`` protocol.

No dependencies beyond the standard library::

    from tricoredb import TriCore

    with TriCore.connect("127.0.0.1", 8427, user="admin", secret="pw") as db:
        db.cache_set("n", "hello", b"world")
        db.execute("CREATE TABLE t (id INT PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO t VALUES (?, ?)", [1, "ada"])
        db.query("SELECT * FROM t").rows      # [["1", "ada"]]

SQL, cache, document, vector, graph and LLM operations are typed methods on
:class:`TriCore`; every one funnels through :meth:`TriCore.request`.
"""

from ._frame import (
    DEFAULT_PORT,
    MAX_CONTROL_FRAME_SIZE,
    MAX_FRAME_SIZE,
    MAX_SUPPORTED_FRAME_VERSION,
    PROTOCOL,
    PROTOCOL_VERSION,
    VERSION,
)
from ._frame import encode_body as _encode_body
from ._handshake import (
    FEATURE_CORRELATION_ID,
    FEATURE_SERVER_PARAMS,
    FEATURE_SESSION_TXN,
    FEATURES,
    Features,
)
from ._version import __version__
from .client import TriCore
from .document import Acc, Filter, GroupBy, Stage
from .errors import (
    NOT_LEADER,
    AuthError,
    ConnectionFailed,
    PoolTimeout,
    ProtocolError,
    Timeout,
    TriCoreError,
)
from .llm import DocumentSource, LlmSource, SqlSource
from .params import bind_params, quote_sql, sql_literal
from .params import decimal_param as _decimal_param
from .params import sql_param as _sql_param
from .pool import Pool
from .tls import TlsOptions
from .types import (
    CypherRows,
    GraphPath,
    Page,
    Response,
    Rows,
    StreamEntry,
    Traversal,
    VectorHits,
    VectorMatch,
)

__all__ = [
    "TriCore", "Pool", "Rows", "Response", "TlsOptions", "Features",
    "Filter", "Stage", "Acc", "GroupBy",
    "CypherRows", "Page", "GraphPath", "Traversal", "VectorHits", "VectorMatch",
    "StreamEntry", "SqlSource", "DocumentSource",
    "bind_params", "sql_literal", "quote_sql",
    "TriCoreError", "AuthError", "ProtocolError", "PoolTimeout", "ConnectionFailed",
    "Timeout", "NOT_LEADER",
]

_ = (
    DEFAULT_PORT, MAX_CONTROL_FRAME_SIZE, MAX_FRAME_SIZE, MAX_SUPPORTED_FRAME_VERSION,
    PROTOCOL, PROTOCOL_VERSION, VERSION, FEATURE_CORRELATION_ID, FEATURE_SERVER_PARAMS,
    FEATURE_SESSION_TXN, FEATURES, LlmSource, __version__, _encode_body, _decimal_param,
    _sql_param,
)
del _
