"""TLS configuration."""

from __future__ import annotations

import socket
import ssl
from typing import Optional

from .errors import TriCoreError


class TlsOptions:
    """How a TLS connection is established.

    TLS is off unless a ``TlsOptions`` is passed. Once on, the server
    certificate is verified and the hostname is checked unless
    ``danger_accept_invalid_certs`` is set.

    With no ``ca_file`` the trust store is empty: the OS roots are deliberately
    not loaded, so a mistyped ``ca_file`` fails instead of trusting a public CA.
    For mTLS set both ``client_cert_file`` and ``client_key_file``; setting one
    alone is an error. Paths are handed to :mod:`ssl`, so key contents never
    enter this driver or its error messages.
    """

    __slots__ = (
        "ca_file",
        "server_name",
        "danger_accept_invalid_certs",
        "client_cert_file",
        "client_key_file",
    )

    def __init__(
        self,
        ca_file: Optional[str] = None,
        server_name: str = "localhost",
        danger_accept_invalid_certs: bool = False,
        client_cert_file: Optional[str] = None,
        client_key_file: Optional[str] = None,
    ) -> None:
        self.ca_file = ca_file
        self.server_name = server_name
        self.danger_accept_invalid_certs = danger_accept_invalid_certs
        self.client_cert_file = client_cert_file
        self.client_key_file = client_key_file

    def _context(self) -> ssl.SSLContext:
        # PROTOCOL_TLS_CLIENT: hostname checking and CERT_REQUIRED on, empty trust store.
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

        if (self.client_cert_file is None) != (self.client_key_file is None):
            missing = "client_key_file" if self.client_key_file is None else "client_cert_file"
            raise TriCoreError(
                f"tls {missing} is required alongside the other (both are needed for mTLS)"
            )

        if self.danger_accept_invalid_certs:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        elif self.ca_file is not None:
            try:
                ctx.load_verify_locations(cafile=self.ca_file)
            except (OSError, ssl.SSLError) as e:
                raise TriCoreError(f"tls ca_file `{self.ca_file}`: {e}") from None

        if self.client_cert_file is not None:
            try:
                ctx.load_cert_chain(self.client_cert_file, self.client_key_file)
            except (OSError, ssl.SSLError) as e:
                raise TriCoreError(
                    f"tls client_cert_file `{self.client_cert_file}` / "
                    f"client_key_file `{self.client_key_file}`: {e}"
                ) from None

        return ctx

    def wrap(self, sock: socket.socket) -> ssl.SSLSocket:
        """Perform the TLS handshake over an already-connected socket."""
        ctx = self._context()
        try:
            return ctx.wrap_socket(sock, server_hostname=self.server_name)
        except ssl.SSLError as e:
            sock.close()
            raise TriCoreError(f"tls handshake with `{self.server_name}` failed: {e}") from None
