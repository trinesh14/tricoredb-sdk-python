"""The conformance runner's stdout protocol: one JSON object per step, nothing else."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

RUNNER = Path(__file__).resolve().parents[1] / "conformance" / "runner.py"


def run(host: str, port: int, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    r = subprocess.run(
        [sys.executable, str(RUNNER), host, str(port), "conformance", "pw"],
        input=json.dumps({"steps": steps}).encode("utf-8"),
        capture_output=True, timeout=120,
    )
    assert r.returncode == 0, r.stderr.decode(errors="replace")
    lines = [ln for ln in r.stdout.decode("utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def test_manifest_shape() -> None:
    manifest = json.loads((RUNNER.parent / "runner.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "python"
    assert manifest["run"] == ["python", "conformance/runner.py"]
    assert manifest["build"] == []


def test_connect_failure_reports_every_step() -> None:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    out = run("127.0.0.1", port, [{"id": "a", "action": "cache.ping", "args": {}},
                                  {"id": "b", "action": "cache.ping", "args": {}}])
    assert [o["id"] for o in out] == ["a", "b"]
    assert all(o["status"] == "error" and "connect failed" in o["error"] for o in out)


@pytest.mark.live
def test_steps_map_to_sdk_calls(server: Any) -> None:
    steps = [
        {"id": "set", "action": "cache.set", "args": {"namespace": "rp", "key": "k", "value": "héllo"}},
        {"id": "get", "action": "cache.get", "args": {"namespace": "rp", "key": "k"}},
        {"id": "miss", "action": "cache.get", "args": {"namespace": "rp", "key": "absent"}},
        {"id": "x1", "action": "cache.xAdd", "args": {"namespace": "rp", "key": "s", "fields": [["a", "1"]]}},
        {"id": "x2", "action": "cache.xAdd", "args": {"namespace": "rp", "key": "s", "fields": [["a", "2"]]}},
        {"id": "xr", "action": "cache.xRead", "args": {"namespace": "rp", "key": "s", "afterStep": "x1"}},
        {"id": "wr", "action": "sql.query", "args": {"sql": "INSERT INTO nope VALUES (1)"}},
        {"id": "unk", "action": "nope.frobnicate", "args": {}},
    ]
    out = {o["id"]: o for o in run(server.host, server.port, steps)}
    assert list(out) == [s["id"] for s in steps]
    assert out["set"] == {"id": "set", "status": "ok", "value": {}}
    assert out["get"]["value"] == {"found": True, "value": "héllo"}
    assert out["miss"]["value"] == {"found": False}
    assert out["xr"]["value"]["entries"] == [{"id": out["x2"]["value"]["id"], "fields": [["a", "2"]]}]
    assert out["wr"]["status"] == "error"
    assert out["unk"]["status"] == "unsupported"
