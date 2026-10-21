# Contributing

Start with [SPEC.md](SPEC.md) and [AGENTS.md](AGENTS.md). Keep the same envelope,
failure semantics and lifecycle across Python, Linux Rust and Node TypeScript.
Keep tool-specific events in their tool's event file, not a core event registry.

## Setup and all suites

Use Python 3.10+, a Linux Rust toolchain, and Node 22.18+ with npm:

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e .
python3 -m unittest
cargo test --manifest-path crates/flattrace/Cargo.toml
npm --prefix typescript ci
npm --prefix typescript run build
npm --prefix typescript test
python3 -m unittest discover -s bench -p 'test_*.py'
cargo test --manifest-path bench/rust/Cargo.toml
```

Rust/Node tests invoke the Python checker from the checkout; run them with
`python3` available. With a populated Cargo cache, add `--offline --locked`.
Set `CARGO_HOME` or `CARGO_TARGET_DIR` only if your environment needs them.
The benchmark adapter checks compile and test the harness, not a timing claim;
see [bench/README.md](bench/README.md) for deliberate performance measurements.

## Package checks (no publication)

```sh
uv build
uv publish --dry-run dist/*
cargo package --manifest-path crates/flattrace/Cargo.toml --list
cargo publish --manifest-path crates/flattrace/Cargo.toml --dry-run
npm --prefix typescript pack --dry-run
```

The npm prepack step builds JS and declarations and stages the shared README
and MIT license. Python uses the exactly pinned uv build backend, including only the Python
module, project metadata, README and license; archive ownership and timestamps
are normalized by the backend.
Cargo stages the shared README automatically; the crate carries a copy of the MIT license.
Run the README examples against the resulting artifacts outside the checkout.
Review archive members and metadata for unrelated files and private data.
No registry publication is implied by a build or dry run.

For the manually dispatched, hash-approved trusted-publishing workflow, see
[Releases](docs/releases.md).

## Changes

Keep PRs focused and explain the observed behavior and how you verified it.
Add regressions for reproduced defects, including parity/legacy coverage where
needed. Never add real credentials or raw private traces. Normative changes
need a spec change before implementation; proposals belong in [ROADMAP.md](ROADMAP.md).
