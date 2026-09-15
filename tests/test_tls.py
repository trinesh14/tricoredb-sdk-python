"""TLS and mTLS against real TLS-enabled servers, with certificates made by openssl."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Optional

import pytest
from _server import ServerProcess

from tricoredb import Pool, TlsOptions, TriCore, TriCoreError

pytestmark = pytest.mark.live


def _openssl() -> Optional[str]:
    found = shutil.which("openssl")
    if found:
        return found
    git = Path(r"C:\Program Files\Git\usr\bin\openssl.exe")
    return str(git) if git.is_file() else None


def _run(openssl: str, *args: str, cwd: Path) -> None:
    env = {**os.environ, "MSYS_NO_PATHCONV": "1", "MSYS2_ARG_CONV_EXCL": "*"}
    r = subprocess.run([openssl, *args], cwd=cwd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"openssl {' '.join(args)} failed:\n{r.stdout}\n{r.stderr}")


def _ca(openssl: str, d: Path, name: str) -> None:
    _run(openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{name}.key",
         "-out", f"{name}.pem", "-days", "2", "-subj", f"/CN={name}",
         "-addext", "basicConstraints=critical,CA:TRUE",
         "-addext", "keyUsage=critical,keyCertSign,cRLSign", cwd=d)


def _leaf(openssl: str, d: Path, name: str, ext: str) -> None:
    (d / f"{name}.ext").write_text(ext, encoding="ascii")
    _run(openssl, "req", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{name}.key",
         "-out", f"{name}.csr", "-subj", f"/CN={name}", cwd=d)
    _run(openssl, "x509", "-req", "-in", f"{name}.csr", "-CA", "ca.pem", "-CAkey", "ca.key",
         "-CAcreateserial", "-out", f"{name}.pem", "-days", "2", "-extfile", f"{name}.ext", cwd=d)


@pytest.fixture(scope="module")
def certs(tmp_path_factory: pytest.TempPathFactory) -> Path:
    openssl = _openssl()
    if openssl is None:
        pytest.skip("openssl not found; cannot mint test certificates")
    d = tmp_path_factory.mktemp("tls")
    _ca(openssl, d, "ca")
    _ca(openssl, d, "other-ca")
    _leaf(openssl, d, "server",
          "subjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=CA:FALSE\n"
          "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n")
    _leaf(openssl, d, "client",
          "basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
          "extendedKeyUsage=clientAuth\n")
    return d


@pytest.fixture(scope="module")
def tls_server(server_binary: str, certs: Path) -> Iterator[ServerProcess]:
    with ServerProcess(server_binary, "sdk-python-tls",
                       tls={"cert_file": certs / "server.pem", "key_file": certs / "server.key"}) as s:
        yield s


@pytest.fixture(scope="module")
def mtls_server(server_binary: str, certs: Path) -> Iterator[ServerProcess]:
    with ServerProcess(server_binary, "sdk-python-mtls",
                       tls={"cert_file": certs / "server.pem", "key_file": certs / "server.key",
                            "ca_file": certs / "ca.pem", "require_client_cert": True}) as s:
        yield s


def test_verified_tls_round_trip(tls_server: Any, certs: Path) -> None:
    tls = TlsOptions(ca_file=str(certs / "ca.pem"), server_name="localhost")
    with TriCore.connect(tls_server.host, tls_server.port, user="admin", secret="pw", tls=tls) as db:
        db.ping()
        db.cache_set("pytls", "k", b"hello-tls")
        assert db.cache_get("pytls", "k") == b"hello-tls"
        big = bytes(range(256)) * 512
        db.cache_set("pytls", "big", big)
        assert db.cache_get("pytls", "big") == big


def test_untrusted_ca_is_refused(tls_server: Any, certs: Path) -> None:
    with pytest.raises(TriCoreError):
        TriCore.connect(tls_server.host, tls_server.port, user="admin", secret="pw",
                        tls=TlsOptions(ca_file=str(certs / "other-ca.pem"), server_name="localhost"))


def test_hostname_mismatch_is_refused(tls_server: Any, certs: Path) -> None:
    with pytest.raises(TriCoreError):
        TriCore.connect(tls_server.host, tls_server.port, user="admin", secret="pw",
                        tls=TlsOptions(ca_file=str(certs / "ca.pem"), server_name="wrong.example.com"))


def test_empty_trust_store_is_refused(tls_server: Any) -> None:
    with pytest.raises(TriCoreError):
        TriCore.connect(tls_server.host, tls_server.port, user="admin", secret="pw", tls=TlsOptions())


def test_danger_flag_is_the_only_escape_hatch(tls_server: Any, certs: Path) -> None:
    tls = TlsOptions(ca_file=str(certs / "other-ca.pem"), danger_accept_invalid_certs=True)
    with TriCore.connect(tls_server.host, tls_server.port, user="admin", secret="pw", tls=tls) as db:
        db.ping()


def test_plaintext_client_cannot_talk_to_tls_server(tls_server: Any) -> None:
    with pytest.raises((TriCoreError, OSError)):
        TriCore.connect(tls_server.host, tls_server.port, user="admin", secret="pw", timeout=5.0)


def test_pool_carries_tls(tls_server: Any, certs: Path) -> None:
    tls = TlsOptions(ca_file=str(certs / "ca.pem"), server_name="localhost")
    with Pool(tls_server.host, tls_server.port, user="admin", secret="pw", size=2, tls=tls) as pool:
        with pool.get() as db:
            db.cache_set("pytls", "pooled", b"via-pool")
            assert db.cache_get("pytls", "pooled") == b"via-pool"


def test_mtls_client_certificate(mtls_server: Any, certs: Path) -> None:
    mtls = TlsOptions(ca_file=str(certs / "ca.pem"), server_name="localhost",
                      client_cert_file=str(certs / "client.pem"), client_key_file=str(certs / "client.key"))
    with TriCore.connect(mtls_server.host, mtls_server.port, user="admin", secret="pw", tls=mtls) as db:
        db.ping()
        db.cache_set("pymtls", "k", b"v")
        assert db.cache_get("pymtls", "k") == b"v"


def test_mtls_without_client_certificate_is_refused(mtls_server: Any, certs: Path) -> None:
    with pytest.raises((TriCoreError, OSError)):
        with TriCore.connect(mtls_server.host, mtls_server.port, user="admin", secret="pw",
                             tls=TlsOptions(ca_file=str(certs / "ca.pem"), server_name="localhost")) as db:
            db.ping()


def test_half_an_identity_is_a_configuration_error(certs: Path) -> None:
    with pytest.raises(TriCoreError, match="client_key_file"):
        TlsOptions(ca_file=str(certs / "ca.pem"), client_cert_file=str(certs / "client.pem"))._context()
