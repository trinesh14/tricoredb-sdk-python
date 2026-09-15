"""Start and stop a private tricore-server for live tests.

Each server gets an ephemeral port (read back from its startup line), its own
temporary data directory, and is stopped by its own PID only.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import IO, Any, Optional

_EXE = "tricore-server.exe" if os.name == "nt" else "tricore-server"
_LISTEN = re.compile(r"listening on\s+([0-9.]+):(\d+)")


def find_server_binary() -> Optional[str]:
    """``TRICORE_SERVER_BIN``, then ``PATH``, then a sibling tricore-db checkout."""
    env = os.environ.get("TRICORE_SERVER_BIN")
    if env:
        return env if Path(env).is_file() else None
    found = shutil.which(_EXE)
    if found:
        return found
    repo = Path(__file__).resolve().parents[1]
    for base in (repo.parent.parent / "tricore" / "tricore-db", repo.parent / "tricore-db"):
        for profile in ("release", "debug"):
            candidate = base / "target" / profile / _EXE
            if candidate.is_file():
                return str(candidate)
    return None


def _config(data_dir: Path, node_id: str, tls: Optional[dict[str, Any]]) -> str:
    tls_block = "[tls]\nenabled = false\n"
    if tls:
        tls_block = (
            "[tls]\nenabled = true\n"
            f'cert_file = "{Path(tls["cert_file"]).as_posix()}"\n'
            f'key_file = "{Path(tls["key_file"]).as_posix()}"\n'
        )
        if tls.get("ca_file"):
            tls_block += f'ca_file = "{Path(tls["ca_file"]).as_posix()}"\n'
        if tls.get("require_client_cert"):
            tls_block += "require_client_cert = true\n"
    return f"""[server]
host = "127.0.0.1"
port = 0
node_id = "{node_id}"
region_id = "sdk-python-tests"
shutdown_grace_secs = 1

[modules]
sql = true
document = true
cache = true
vector = true
graph = true
llm = true
cluster = true

[storage]
data_dir = "{data_dir.as_posix()}"
fsync = false

[security]
auth_mode = "password"
dev_auth = true

[observability]
port = 0

{tls_block}"""


class ServerProcess:
    def __init__(self, binary: str, node_id: str = "sdk-python", tls: Optional[dict[str, Any]] = None):
        self.dir = Path(tempfile.mkdtemp(prefix=f"tricore-{node_id}-"))
        data = self.dir / "data"
        data.mkdir()
        cfg = self.dir / "tricore.toml"
        cfg.write_text(_config(data, node_id, tls), encoding="utf-8")
        self.proc = subprocess.Popen(
            [binary, "--config", str(cfg)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.pid = self.proc.pid
        self.host = "127.0.0.1"
        self.port = 0
        lines: list[str] = []
        deadline = time.monotonic() + 60
        assert self.proc.stdout is not None
        while time.monotonic() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                break
            lines.append(line.rstrip())
            m = _LISTEN.search(line)
            if m:
                self.host, self.port = m.group(1), int(m.group(2))
                break
        if not self.port:
            self.stop()
            raise RuntimeError("tricore-server did not report a port:\n  " + "\n  ".join(lines[-30:]))
        threading.Thread(target=_drain, args=(self.proc.stdout,), daemon=True).start()

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
        shutil.rmtree(self.dir, ignore_errors=True)

    def __enter__(self) -> ServerProcess:
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


def _drain(pipe: IO[str]) -> None:
    try:
        for _ in pipe:
            pass
    except (OSError, ValueError):
        pass
