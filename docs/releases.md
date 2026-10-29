# Release builds and reviewed artifacts

The manually dispatched [release workflow](../.github/workflows/release.yml)
separates building from publishing. **Publish targets never rebuild.** A local
build or a successful workflow is not approval to publish.

## Build once

Dispatch `target=build` with `ref` set to the selected tag or full commit SHA.
Native build jobs and the assembly job run only for this target. Assembly runs
the Python, benchmark, Rust and Node suites before packaging. It uploads the
`release-distributions` artifact containing `source-commit.txt`, `SHA256SUMS`,
Python wheels under `python/`, the npm tarball under `npm/`, and CLI binaries
under `cli/`. Save the successful workflow run's numeric ID as `build_run_id`.

There are two Python wheel build paths:

- **Native:** the checkout uses pinned `maturin` to build `cp310-abi3` wheels
  containing the reader engine, for manylinux2014 x86_64 and macOS arm64.
- **Pure fallback:** assembly temporarily replaces maturin with pinned
  `uv_build`, removes the native extension built by tests, and builds a
  `py3-none-any` wheel with no `.so`, `.pyd` or `.dylib` files. The writer and
  check/export/stats work without Rust; native reader functions raise
  `EngineUnavailable`. The original checkout backend is restored afterward.

Review the downloaded artifacts themselves: package members, metadata, source
commit, test results and SHA-256 values. Retain those exact reviewed bytes;
native rebuilds need not have the same hash.

## Publish the reviewed bytes

Dispatch the appropriate target with the same `ref`, the reviewed
`build_run_id`, and the reviewed SHA-256 values:

| Target | Required hash inputs |
|---|---|
| `pypi` | `expected_abi3_linux_sha256`, `expected_abi3_macos_sha256`, `expected_pure_sha256` |
| `npm` | `expected_tgz_sha256` |
| `cli` | `expected_cli_linux_sha256`, `expected_cli_macos_sha256` |

Each publish job checks that the selected build run completed successfully in
this repository using this release workflow, downloads its existing
`release-distributions`, resolves `ref`, compares it to `source-commit.txt`,
and recomputes the required approved hashes before upload. A missing or
mismatched hash fails closed. There is no source checkout or package build in
the publish jobs; npm lifecycle scripts are disabled.

For npm, dispatch the publishing workflow **at the same commit as the artifact
source**. Its reachable pre-upload guard compares `source-commit.txt` to both
the resolved `ref` SHA and the publishing workflow's `github.sha`, because npm
provenance identifies that workflow commit as source. Selecting an older
artifact from a newer workflow revision therefore fails, even if its hash is
approved. Use a tag or branch at the artifact's source commit for the dispatch.
The CLI target requires a release tag rather than a raw SHA.

Registry environments and trusted-publishing identities must be configured
separately. Obtain publication approval for the exact source and artifact hashes
before dispatching a publish target; building and reviewing do not publish them.
