# Release workflow

[The release workflow](../.github/workflows/release.yml) runs only by manual
`workflow_dispatch`. Pushes and tags do not start it. It builds and tests Python,
Rust and Node code without publishing credentials. Separate fresh jobs receive
only the completed build artifacts and the permission needed by their registry.
No stored publishing token is used.

## Forgejo first

Every commit that reaches GitHub must already exist on the private Forgejo
branch `main`, byte for byte. Push Forgejo first, then push the same commit to
GitHub `main` as a fast-forward. Never author or rewrite commits at GitHub push
time. Forgejo carries internal development, issue tracking and planning;
material that must stay private is kept off its `main` branch.

## Review before publishing

1. Select a release commit containing the pinned build configurations
   (maturin 1.15.0 with abi3-py310, uv_build 0.9.27 for the pure fallback,
   @napi-rs/cli 3.10.9, Rust 1.94.1). Use a
   tag name or a full lowercase Git commit SHA as the `ref` input, not a branch.
2. Dispatch with `target: build`. Download `release-distributions`, verify
   `source-commit.txt`, inspect package contents and compare every entry in
   `SHA256SUMS` with the independently checked local build. This target cannot
   publish and does not receive OIDC permission.
3. Obtain explicit approval for the source commit, registry and exact artifact
   hashes. Dispatch with `target: pypi`, `npm` or `cli`, the same `ref`, the
   reviewed build-only run as `build_run_id`, and the reviewed hashes below.
   Publish targets never rebuild (native wheels are not byte-reproducible
   across runs): the publish job checks that the build run is a successful
   run of this workflow, downloads its `release-distributions` artifact,
   checks `source-commit.txt` against `ref`, and recomputes every hash.
   Missing, malformed or different values fail before the publishing client
   requests an OIDC identity or uploads anything. Hashes in the downloaded
   manifest alone are not treated as approval.

| Target | Required approval inputs |
| --- | --- |
| `build` | None |
| `pypi` | `build_run_id`, `expected_abi3_linux_sha256`, `expected_abi3_macos_sha256`, `expected_pure_sha256` |
| `npm` | `build_run_id`, `expected_tgz_sha256` |
| `cli` | `build_run_id`, `expected_cli_linux_sha256`, `expected_cli_macos_sha256` |

Hashes must be exactly 64 lowercase hexadecimal characters. These requirements
are conditional: GitHub's dispatch form cannot make an input required only for
one target, so the workflow validates them before checkout and again in the
publish job. Dispatch permissions remain the authority boundary; hash matching
is not proof that an operator reviewed or approved the bytes.

The `cli` target uploads the two reviewed CLI binaries to the tag's GitHub
release (`gh release upload`, never `--clobber`). It needs a tag ref, not a
commit SHA, and the tag's release must already exist: create it first, then
dispatch. Configure a `cli` environment; the job needs `contents: write` for
the upload plus `id-token: write` (kept for uniformity with the other
publish jobs; no OIDC identity is requested for the GitHub release upload).

For npm, dispatch the workflow itself at the same commit selected by `ref`.
The workflow verifies this so npm's provenance identifies the source actually
built, rather than a different workflow-branch commit.

## Registry setup

PyPI `flattrace` 0.2.0 ships three wheels and no sdist: `cp310-abi3` for
manylinux2014 x86_64 and macOS arm64 (each carrying the compiled reader
engine) plus one `py3-none-any` pure fallback (writer, check/export/stats;
`flattrace.reader` raises `EngineUnavailable`). An sdist would need a Rust
toolchain at install time, so none is published; installers land on the pure
wheel where no abi3 wheel matches. For PyPI's pending trusted publisher,
register project `flattrace`, owner
`kyraneth`, repository `flattrace`, workflow filename `release.yml` and
environment `pypi`. The publish job has only `id-token: write` and runs
`uv publish --trusted-publishing always` on the three approved wheels. It does
not check out or execute release source. uv requires OIDC rather than falling
back to a stored publishing token. This workflow does not generate PyPI
attestations; trusted publishing and artifact attestations are different features.

Publish npm version 0.1.0 manually first. npm cannot create the initial package
through trusted publishing. For later versions, configure the existing package's
trusted publisher with owner `kyraneth`, repository `flattrace`, workflow filename
`release.yml` and environment `npm`, permitting direct `npm publish`. The package
repository URL must continue to match this GitHub repository. The workflow uses
GitHub-hosted runners, Node 24.18.0 with its bundled npm 11.16.0, and
`npm publish <tgz> --provenance --access public`. The 0.2.0 tarball contains
both prebuilt bindings (`native/flattrace-reader.linux-x64-gnu.node` and
`native/flattrace-reader.darwin-arm64.node`); the `flattrace/reader` loader
picks the running platform and throws `EngineUnavailable` naming it otherwise.
There is still one package and no `optionalDependencies`: adding per-platform
packages would need extra package names with manual first publishes, which the
single-package layout avoids. npm requires Node >=22.14.0 and
npm >=11.5.1. Lifecycle scripts are disabled in the publish job. There is no
checkout, dependency installation or package build in that privileged job.

See [npm's trusted publisher documentation](https://docs.npmjs.com/trusted-publishers/),
[PyPI's publishing guide](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
and [uv's publishing guide](https://docs.astral.sh/uv/guides/package/).

## Reproducible local build

Use uv 0.9.27, Python 3.13.11, Node 24.18.0 with npm 11.16.0, maturin 1.15.0,
@napi-rs/cli 3.10.9 and Rust 1.94.1, as in the workflow. From a clean checkout
of the selected commit:

```sh
umask 002
export SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)"
export TZ=UTC PYTHONDONTWRITEBYTECODE=1 CARGO_NET_RETRY=3
python -m pip install --no-cache-dir "maturin==1.15.0"
python -m maturin build --release --out dist/abi3
# Pure fallback: same sources, uv build backend instead of maturin.
python - <<'PY'
from pathlib import Path
text = Path('pyproject.toml').read_text()
text = text.replace('requires = ["maturin==1.15.0"]\nbuild-backend = "maturin"',
                    'requires = ["uv_build==0.9.27"]\nbuild-backend = "uv_build"')
out, skipping = [], False
for line in text.splitlines(keepends=True):
    if line.startswith('['):
        skipping = line.strip() == '[tool.maturin]'
        if skipping:
            continue
    if not skipping:
        out.append(line)
out.append('\n[tool.uv.build-backend]\nmodule-root = ""\nmodule-name = "flattrace"\n')
Path('pyproject.toml').write_text(''.join(out))
PY
uv build --python 3.13.11 --no-sources --out-dir dist/python
cp dist/abi3/*.whl dist/python/
git checkout -- pyproject.toml
npm --prefix typescript ci --ignore-scripts
npm --prefix typescript run build-reader
npm --prefix typescript run build
mkdir -p dist/npm dist/cli
(cd typescript && npm pack --ignore-scripts --pack-destination ../dist/npm)
cargo build --release --locked --manifest-path crates/flattrace-read/Cargo.toml --bin flattrace-read
cp target/release/flattrace-read dist/cli/flattrace-read-linux-x64
(cd dist && sha256sum python/*.whl npm/*.tgz cli/*)
```

The abi3 wheels need manylinux2014 for the Linux tag; build those inside
`quay.io/pypa/manylinux2014_x86_64` with Rust 1.94.1 and maturin 1.15.0 exactly
as the workflow does (the container only needs the checkout mounted).
The macOS wheel and the darwin-arm64 binding build on the macos-14 runner;
only linux-x64-gnu can be built on a Linux host. Never invent a mac build
here; the darwin artifacts come from CI.

The engine backend is exactly pinned to `maturin==1.15.0` with `abi3-py310`
in `pyproject.toml` / `crates/flattrace-py/Cargo.toml`; the pure fallback
uses exactly pinned `uv_build==0.9.27` through the documented temporary
backend swap, so a fresh checkout plus the two pins reproduces both wheel
kinds. maturin normalizes its own archive metadata; `SOURCE_DATE_EPOCH`
records the source commit time for the build environment. Python
wheels include only the Python module (writer, check/export/stats,
reader), the compiled extension where applicable, metadata, README and
license. npm dependency versions and integrity hashes come from
`typescript/package-lock.json`; packing happens after the explicit build with
implicit lifecycle scripts disabled, and the tarball carries both `.node`
files under `native/`. CLI binaries are release-profile builds of
`crates/flattrace-read` with `--locked`.

The original 0.1.0 setuptools artifacts are not byte-reproducible from a fresh
checkout: their archive timestamps include local build times and their sdist
stores local filesystem ownership. The pinned native-backend packages therefore
have new hashes and packaging metadata, with unchanged Python runtime source.
Review these new artifacts rather than reusing the old hashes.

## Action pins

Every action is pinned to the commit behind the named upstream release tag.
These were verified against the action's original GitHub repository on
2026-10-21. Caches are disabled in this release workflow.

| Action | Release | Commit |
| --- | --- | --- |
| `actions/checkout` | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `actions/setup-python` | v7.0.0 | `5fda3b95a4ea91299a34e894583c3862153e4b97` |
| `astral-sh/setup-uv` | v10.3.0 | `1c37ad07a6a961277cf70c0d37d6f313000f5884` |
| `actions/setup-node` | v7.1.0 | `949feb2413d6458794dcd2491c4babbbce0c15c1` |
| `actions/upload-artifact` | v7.0.2 | `cf430e030ddbb5b0abf93d22962f4752f3646cd9` |
| `actions/download-artifact` | v8.0.2 | `9000827ccba6bdab643e8b6fd33ac0654aef8333` |

A configured environment name does not itself create an approval gate. Optional
GitHub environment protections can add another gate, but this workflow's required
pre-upload check is the independently supplied artifact hashes. Do not infer
publication permission from a successful build, matching hashes or a tag.
