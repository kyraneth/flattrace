# Changelog

## 0.2.0 (unreleased)

- Rust reader engine (`flattrace-read` crate plus `flattrace-read` CLI):
  streaming check at parity with `flattrace check`, streaming `stats` parity,
  span pairing, disk-spilled exact quantiles and the frozen digest/spans/group/
  tree/compare/overlap query surface over plain and gzip JSONL.
- Python (`flattrace.reader`) and Node (`flattrace/reader`) bindings to the
  engine with named `EngineUnavailable` absence; the pure writer and
  check/export/stats keep working without native code.
- PyPI ships abi3 wheels (manylinux2014 x86_64, macOS arm64) plus the pure
  `py3-none-any` fallback; the npm tarball carries both `.node` files; CLI
  binaries ship on GitHub releases. All behind the existing reviewed-hash
  publish gates; no sdist that needs a Rust toolchain.
- Conformance corpus with golden outputs asserted from Rust, CLI, Python and
  Node; frozen reader contract in `docs/reader.md`.
- Writer run-end data: `close(data=...)` / `finish_with` carry an optional
  run-end payload (e.g. run totals), normalized like any other payload;
  omitted means `{}`. Rust writer crate bumped to 0.2.0.
- Writer `end()` returns the `took_ms` written on the end line (Python and
  TypeScript; Rust keeps `end()` and adds `end_timed()`). Backward compatible.

## 0.1.0

Initial release, published to PyPI, crates.io and npm.

- Python, Linux Rust and Node TypeScript synchronous writers with the same v1
  JSONL envelope, W3C traceparent propagation and one file per process run.
- Explicit span lifecycle, points, outcomes, structured errors, sequence counters,
  honest unfinished evidence and configurable tool-owned detail levels.
- Environment activation with nonfatal sink failures; strict explicit paths;
  sanitized argv and JSON-safe payload snapshots.
- Python CLI: stream/lifecycle and event-contract checking, reviewed redaction
  exports, gzip input and streaming duration/error/run statistics.
- Frozen legacy compatibility and cross-language parity fixtures, nested-process
  and learning examples, overhead benchmark harness and optional learning conventions.
- Repository contributor/agent guidance and an instrumentation/analysis agent skill.
- Registry-ready metadata; npm ships built ESM JavaScript and declarations.
