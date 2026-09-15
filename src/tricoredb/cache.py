"""The cache model: strings, TTLs, counters, lists, sets, hashes, streams.

Values are ``bytes`` throughout; the ``*_text`` variants encode UTF-8. A key
holds one type at a time, and using it as another type is an error.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Optional

from ._base import ClientBase
from .errors import TriCoreError
from .types import StreamEntry, stream_entries


def _bytes(value: Any, name: str) -> list[int]:
    if value is None:
        raise TriCoreError(f"{name} must not be None")
    if isinstance(value, str):
        return list(value.encode("utf-8"))
    if isinstance(value, (bytes, bytearray, memoryview)):
        return list(bytes(value))
    raise TriCoreError(f"{name} must be bytes or str, not {type(value).__name__}")


def _byte_arrays(values: Sequence[Any], name: str) -> list[list[int]]:
    items = list(values)
    if not items:
        raise TriCoreError(f"{name} must be a non-empty sequence")
    return [_bytes(v, f"{name} element") for v in items]


def _pairs(entries: Sequence[Any], name: str) -> list[list[list[int]]]:
    items = list(entries)
    if not items:
        raise TriCoreError(f"{name} must be a non-empty sequence of (field, value) pairs")
    out = []
    for e in items:
        if not isinstance(e, (tuple, list)) or len(e) != 2:
            raise TriCoreError(f"each {name} entry must be a (field, value) pair")
        out.append([_bytes(e[0], f"{name} field"), _bytes(e[1], f"{name} value")])
    return out


def _text_pairs(mapping: Mapping[str, str]) -> list[tuple[bytes, bytes]]:
    return [(k.encode("utf-8"), str(v).encode("utf-8")) for k, v in mapping.items()]


class CacheMixin(ClientBase):
    def cache_ping(self, database: str = "main") -> None:
        """Liveness through the cache core (auth, routing and dispatch)."""
        self.request({"Cache": "Ping"}, database)

    def cache_set(
        self,
        namespace: str,
        key: str,
        value: bytes,
        ttl_ms: Optional[int] = None,
        database: str = "main",
    ) -> None:
        self.request(
            {"Cache": {"Set": {"namespace": namespace, "key": key,
                               "value": _bytes(value, "value"), "ttl_ms": ttl_ms}}},
            database,
        )

    def cache_get(self, namespace: str, key: str, database: str = "main") -> Optional[bytes]:
        """The value, or ``None`` on a miss."""
        return self._cache_value({"Cache": {"Get": {"namespace": namespace, "key": key}}}, database)

    def cache_get_text(self, namespace: str, key: str, database: str = "main") -> Optional[str]:
        v = self.cache_get(namespace, key, database)
        return None if v is None else v.decode("utf-8")

    def cache_delete(self, namespace: str, key: str, database: str = "main") -> bool:
        return bool(self._json(
            {"Cache": {"Delete": {"namespace": namespace, "key": key}}}, database)["deleted"])

    def cache_exists(self, namespace: str, key: str, database: str = "main") -> bool:
        return bool(self._json(
            {"Cache": {"Exists": {"namespace": namespace, "key": key}}}, database)["exists"])

    def cache_ttl(self, namespace: str, key: str, database: str = "main") -> Optional[int]:
        """Remaining TTL in ms; ``None`` when the key is missing or has no expiry."""
        ttl = self._json({"Cache": {"Ttl": {"namespace": namespace, "key": key}}}, database)["ttl_ms"]
        return ttl if isinstance(ttl, int) else None

    def cache_clear_namespace(self, namespace: str, database: str = "main") -> int:
        return int(self._json(
            {"Cache": {"ClearNamespace": {"namespace": namespace}}}, database)["cleared"])

    def cache_incr(self, namespace: str, key: str, by: int = 1, database: str = "main") -> int:
        """Add to a counter; a missing key starts at 0."""
        return int(self._json(
            {"Cache": {"Incr": {"namespace": namespace, "key": key, "by": by}}}, database)["value"])

    def cache_expire(self, namespace: str, key: str, ttl_ms: int, database: str = "main") -> bool:
        """Set or replace a TTL. ``False`` when the key does not exist."""
        return bool(self._json(
            {"Cache": {"Expire": {"namespace": namespace, "key": key, "ttl_ms": ttl_ms}}},
            database)["updated"])

    def cache_persist(self, namespace: str, key: str, database: str = "main") -> bool:
        """Drop a TTL. ``False`` when it had none."""
        return bool(self._json(
            {"Cache": {"Persist": {"namespace": namespace, "key": key}}}, database)["persisted"])

    def cache_set_nx(
        self,
        namespace: str,
        key: str,
        value: bytes,
        ttl_ms: Optional[int] = None,
        database: str = "main",
    ) -> bool:
        """Set only if absent."""
        return bool(self._json(
            {"Cache": {"SetNx": {"namespace": namespace, "key": key,
                                 "value": _bytes(value, "value"), "ttl_ms": ttl_ms}}},
            database)["set"])

    def cache_keys(
        self,
        namespace: str,
        pattern: Optional[str] = None,
        limit: Optional[int] = None,
        database: str = "main",
    ) -> list[dict[str, Any]]:
        """Live keys with TTL and size. ``pattern`` is a glob where ``*`` matches any run."""
        return list(self._json(
            {"Cache": {"Keys": {"namespace": namespace, "pattern": pattern, "limit": limit}}},
            database).get("keys", []))

    def cache_lpush(self, namespace: str, key: str, values: Sequence[bytes],
                    database: str = "main") -> int:
        return self._push("LPush", namespace, key, values, database)

    def cache_rpush(self, namespace: str, key: str, values: Sequence[bytes],
                    database: str = "main") -> int:
        return self._push("RPush", namespace, key, values, database)

    def _push(self, variant: str, namespace: str, key: str, values: Sequence[bytes],
              database: str) -> int:
        return int(self._json(
            {"Cache": {variant: {"namespace": namespace, "key": key,
                                 "values": _byte_arrays(values, "values")}}},
            database)["length"])

    def cache_lpop(self, namespace: str, key: str, database: str = "main") -> Optional[bytes]:
        return self._cache_value({"Cache": {"LPop": {"namespace": namespace, "key": key}}}, database)

    def cache_rpop(self, namespace: str, key: str, database: str = "main") -> Optional[bytes]:
        return self._cache_value({"Cache": {"RPop": {"namespace": namespace, "key": key}}}, database)

    def cache_lrange(self, namespace: str, key: str, start: int, stop: int,
                     database: str = "main") -> list[bytes]:
        """An inclusive index range; negative indices count from the end."""
        return [bytes(v) for v in self._json(
            {"Cache": {"LRange": {"namespace": namespace, "key": key,
                                  "start": start, "stop": stop}}},
            database)["values"]]

    def cache_llen(self, namespace: str, key: str, database: str = "main") -> int:
        return int(self._json(
            {"Cache": {"LLen": {"namespace": namespace, "key": key}}}, database)["length"])

    def cache_lindex(self, namespace: str, key: str, index: int,
                     database: str = "main") -> Optional[bytes]:
        return self._cache_value(
            {"Cache": {"LIndex": {"namespace": namespace, "key": key, "index": index}}}, database)

    def cache_sadd(self, namespace: str, key: str, members: Sequence[bytes],
                   database: str = "main") -> int:
        return int(self._json(
            {"Cache": {"SAdd": {"namespace": namespace, "key": key,
                                "members": _byte_arrays(members, "members")}}},
            database)["added"])

    def cache_srem(self, namespace: str, key: str, members: Sequence[bytes],
                   database: str = "main") -> int:
        return int(self._json(
            {"Cache": {"SRem": {"namespace": namespace, "key": key,
                                "members": _byte_arrays(members, "members")}}},
            database)["removed"])

    def cache_sismember(self, namespace: str, key: str, member: bytes,
                        database: str = "main") -> bool:
        return bool(self._json(
            {"Cache": {"SIsMember": {"namespace": namespace, "key": key,
                                     "member": _bytes(member, "member")}}},
            database)["is_member"])

    def cache_scard(self, namespace: str, key: str, database: str = "main") -> int:
        return int(self._json(
            {"Cache": {"SCard": {"namespace": namespace, "key": key}}}, database)["cardinality"])

    def cache_smembers(self, namespace: str, key: str, database: str = "main") -> list[bytes]:
        """Every member, in ascending byte order."""
        return [bytes(v) for v in self._json(
            {"Cache": {"SMembers": {"namespace": namespace, "key": key}}}, database)["members"]]

    def cache_hset(
        self,
        namespace: str,
        key: str,
        entries: Sequence[tuple[bytes, bytes]],
        database: str = "main",
    ) -> int:
        """Set ``(field, value)`` pairs; returns how many fields were newly created."""
        return int(self._json(
            {"Cache": {"HSet": {"namespace": namespace, "key": key,
                                "entries": _pairs(entries, "entries")}}},
            database)["created"])

    def cache_hset_text(self, namespace: str, key: str, entries: Mapping[str, str],
                        database: str = "main") -> int:
        return self.cache_hset(namespace, key, _text_pairs(entries), database)

    def cache_hget(self, namespace: str, key: str, field: bytes,
                   database: str = "main") -> Optional[bytes]:
        return self._cache_value(
            {"Cache": {"HGet": {"namespace": namespace, "key": key,
                                "field": _bytes(field, "field")}}}, database)

    def cache_hdel(self, namespace: str, key: str, fields: Sequence[bytes],
                   database: str = "main") -> int:
        return int(self._json(
            {"Cache": {"HDel": {"namespace": namespace, "key": key,
                                "fields": _byte_arrays(fields, "fields")}}},
            database)["deleted"])

    def cache_hgetall(self, namespace: str, key: str,
                      database: str = "main") -> list[tuple[bytes, bytes]]:
        """Every ``(field, value)`` pair, in ascending field order."""
        return [(bytes(f), bytes(v)) for f, v in self._json(
            {"Cache": {"HGetAll": {"namespace": namespace, "key": key}}},
            database).get("entries", [])]

    def cache_hexists(self, namespace: str, key: str, field: bytes,
                      database: str = "main") -> bool:
        return bool(self._json(
            {"Cache": {"HExists": {"namespace": namespace, "key": key,
                                   "field": _bytes(field, "field")}}},
            database)["exists"])

    def cache_hlen(self, namespace: str, key: str, database: str = "main") -> int:
        return int(self._json(
            {"Cache": {"HLen": {"namespace": namespace, "key": key}}}, database)["length"])

    def cache_xadd(
        self,
        namespace: str,
        key: str,
        fields: Sequence[tuple[bytes, bytes]],
        entry_id: Optional[str] = None,
        database: str = "main",
    ) -> str:
        """Append an entry and return its id. ``entry_id`` is ``None``/``'*'``,
        ``'<ms>'``, ``'<ms>-*'`` or ``'<ms>-<seq>'``; it must increase."""
        return str(self._json(
            {"Cache": {"XAdd": {"namespace": namespace, "key": key, "id": entry_id,
                                "fields": _pairs(fields, "fields")}}},
            database)["id"])

    def cache_xadd_text(self, namespace: str, key: str, fields: Mapping[str, str],
                        entry_id: Optional[str] = None, database: str = "main") -> str:
        return self.cache_xadd(namespace, key, _text_pairs(fields), entry_id, database)

    def cache_xlen(self, namespace: str, key: str, database: str = "main") -> int:
        return int(self._json(
            {"Cache": {"XLen": {"namespace": namespace, "key": key}}}, database)["length"])

    def cache_xrange(
        self,
        namespace: str,
        key: str,
        start: str = "-",
        end: str = "+",
        count: Optional[int] = None,
        database: str = "main",
    ) -> list[StreamEntry]:
        """Entries whose id falls in the inclusive range (``-``/``+`` are min/max)."""
        return stream_entries(self._json(
            {"Cache": {"XRange": {"namespace": namespace, "key": key,
                                  "start": start, "end": end, "count": count}}},
            database))

    def cache_xread(
        self,
        namespace: str,
        key: str,
        after: str = "0-0",
        count: Optional[int] = None,
        database: str = "main",
    ) -> list[StreamEntry]:
        """Entries strictly newer than ``after``. Never blocks."""
        return stream_entries(self._json(
            {"Cache": {"XRead": {"namespace": namespace, "key": key,
                                 "after": after, "count": count}}},
            database))

    def cache_xdel(self, namespace: str, key: str, ids: Sequence[str],
                   database: str = "main") -> int:
        return int(self._json(
            {"Cache": {"XDel": {"namespace": namespace, "key": key, "ids": list(ids)}}},
            database)["deleted"])

    def cache_xtrim(self, namespace: str, key: str, max_len: int,
                    database: str = "main") -> int:
        """Evict the oldest entries down to ``max_len``; returns how many went."""
        return int(self._json(
            {"Cache": {"XTrim": {"namespace": namespace, "key": key, "max_len": max_len}}},
            database)["trimmed"])
