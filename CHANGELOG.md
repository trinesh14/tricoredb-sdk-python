# Changelog

All notable changes to this package are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-09-15

First release on PyPI.

### Added

- Native TriCoreDB protocol client for Python, using only the standard library:
  no runtime dependencies.
- Fully typed (`py.typed`), checked with `mypy --strict`.
- SQL: `query()` and `execute()` with server-side `?` parameters. `Decimal`,
  `datetime`, `UUID` and binary values are converted exactly.
- Transactions: one-request scripts (`transaction`) and session blocks
  (`begin`, `commit`, `rollback`, `transaction_block`).
- Connection pool (`Pool`) that never returns a connection with an open
  transaction.
- Documents, vectors, graphs, cache (keys, lists, sets, hashes, streams), LLM
  context and admin operations.
- Typed errors with stable codes (`e.code`) and cluster redirect hints
  (`e.is_redirect`, `e.leader_hint`).
- TLS and mutual TLS through `TlsOptions`.

### Security

- Operations that need a server capability which was not granted (server-side
  parameters, session transactions) fail with a clear error before anything is
  sent, instead of silently falling back.
- With TLS and no `ca_file`, the trust store is empty: the client never falls
  back to operating-system root certificates.

[Unreleased]: https://github.com/trinesh14/tricoredb-sdk-python/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/trinesh14/tricoredb-sdk-python/releases/tag/v0.1.0
