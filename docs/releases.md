# Release builds and reviewed artifacts

The manually dispatched [release workflow](../.github/workflows/release.yml)
separates building from publication. A successful build is not publication approval.

## Build and review

Dispatch `target=build` at the exact release tag or full commit SHA. Assembly
runs the Python, benchmark, Rust and Node suites and uploads
`release-distributions`: `source-commit.txt`, `SHA256SUMS`, Python wheels,
the npm tarball, CLI binaries and the two Rust `.crate` archives. Retain the
successful run ID and download and review the actual members, metadata,
source commit, tests and hashes. Native rebuilds need not be byte-identical.
The native Python wheels contain the reader engine; the pure fallback does not.

## Publish

The `pypi` and `cli` workflow targets consume the reviewed build's bytes,
checking the run, source ref and supplied SHA-256 hashes before uploading.
`pypi` uses GitHub environment `pypi` and trusted publishing; `cli` uses
`cli` and requires a release tag. Supply `build_run_id` and the relevant
`expected_abi3_linux_sha256`, `expected_abi3_macos_sha256`,
`expected_pure_sha256`, or `expected_cli_linux_sha256` and
`expected_cli_macos_sha256` inputs.

npm is published outside CI with the security-key `npm-public-publish` route
using the reviewed npm archive, SHA-256 and SHA-512 integrity. Crates are
published outside CI with `cargo publish` from the exact reviewed tag using
the scoped Proton Pass route. Cargo rebuilds its own archive: compare package
contents and generated archive hashes against the reviewed build before sending;
stop if they differ. Publish the writer before the reader. No GitHub npm or
crates environment or CI token is required. Never retry an uncertain upload
before inspecting the destination registry.
