# Working in flattrace

- Layout: `flattrace/` is the Python writer and check/export/stats CLI;
  `crates/flattrace/` is the Linux Rust writer; `typescript/` is the Node
  writer and npm build. `schema/`, `examples/`, `tests/`, `bench/` and
  `skills/flattrace/` hold the schema, examples, tests, overhead harness and
  consumer skill respectively.
- **[SPEC.md](SPEC.md) is normative.** The JSON Schema covers only the envelope.
  Payload conventions and ROADMAP proposals do not silently extend the contract.
- Preserve the one flat v1 line shape and Python/Rust/TypeScript parity.
  Legacy v1 without seq/pid/new payload fields remains valid. Missing ends
  remain unfinished; only close/finish ends the run, after local spans end.
- Never harm product work: environment sink I/O failures warn and disable tracing;
  explicit paths stay strict. Do not swallow structural errors or product errors,
  fabricate success, scrape argv/environment, delete traces or add uploads.
- Never change envelope semantics, activation, lifecycle, levels, error handling
  or compatibility without updating the spec and coordinating all three writers.
  A breaking format change requires a version bump, not a silent v1 alteration.
- Fixtures must be synthetic or explicitly reviewed exports, without private
  identities, paths, hosts or real credentials. Keep frozen legacy bytes intact.
- Run `python3 -m unittest`, `cargo test --manifest-path crates/flattrace/Cargo.toml`
  and `npm --prefix typescript test`; see [CONTRIBUTING.md](CONTRIBUTING.md)
  for build/package and benchmark checks. Test the CLI against emitted traces
  after writer changes; inspect all package contents after packaging changes.
