# Changelog

## 0.3.0

- Composition checker (#10): `check(paths, events=[...], command?)` and
  repeatable `--events` validate a run against the union of the supplied
  vocabularies in Python, the Rust engine and CLI, and both bindings.
  Byte-identical files dedupe; same-name definition conflicts (even
  description-only), duplicate tool owners and duplicate command IDs fail
  naming both files; a run-start `data.vocabularies` manifest switches to
  composition mode (missing, mismatched and extra files fail and are
  named); without a manifest the given files compose and the run reports
  unverified provenance as a non-failing notice. Required/command rules
  stay scoped to the owning vocabulary. One SPEC MAY line; reserved `run`
  declaration in `schema/run-declaration.json`; truth table in
  `docs/reference.md`; check contract revision in `docs/reader.md`.
- Unicode policy (#11): writers emit only valid Unicode scalar strings,
  replacing lone surrogates with U+FFFD plus a note (valid pairs stay;
  colliding keys keep the first with a note). Legacy lone-surrogate
  escapes parse leniently and every checker reports the same
  warning-level notice. Export writes to a temp name and renames on
  success, serializes surrogate-safe, and never leaves a plausible
  partial file.
- Rust writer `try_finish(&mut self, outcome, data)` keeps state on a
  refused close so the caller can end the child span and close (#12).
- Detector/annotation/action/changes conventions and the glossary in
  `docs/learning.md` (#7); verdict unchanged.
- Conventions vocabulary (#14): `schema/conventions-events.json` (tool
  `flattrace-conventions`) declares the reserved `run` span plus
  `annotate`, `annotation`, `verdict`, `action` and `changes` (`action`
  and `changes` at `level: 2`); convention names are used with the
  convention's meaning and never redefined. Conformance fixtures cover
  a composed conventions run and a conflicting `annotation`
  redefinition; shipped in the npm package and the Python wheel.
- Conformance fixtures for every checker case plus regenerated goldens,
  asserted from Rust, CLI, Python and Node.
- Generic README rewrite of the vocabularies explanation with executed
  examples, plus the `scripts/export-manifest` and `scripts/check-public-tree`
  publication gates (#9, #8).

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
