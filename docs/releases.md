# Release workflow

[The release workflow](../.github/workflows/release.yml) runs only by manual
`workflow_dispatch`. Pushes and tags do not start it. It builds and tests Python,
Rust and Node code without publishing credentials. Separate fresh jobs receive
only the completed build artifacts and the permission needed by their registry.
No stored publishing token is used.

## Review before publishing

1. Select a release commit containing the pinned `uv_build` configuration. Use a
   tag name or a full lowercase Git commit SHA as the `ref` input, not a branch.
2. Dispatch with `target: build`. Download `release-distributions`, verify
   `source-commit.txt`, inspect package contents and compare every entry in
   `SHA256SUMS` with the independently checked local build. This target cannot
   publish and does not receive OIDC permission.
3. Obtain explicit approval for the source commit, registry and exact artifact
   hashes. Dispatch the same `ref` with `target: pypi` or `target: npm`, supplying
   the reviewed hashes in the inputs below. The workflow rebuilds, then each
   publish job independently recomputes hashes of its downloaded files. Missing,
   malformed or different hashes fail before the publishing client requests an
   OIDC identity or uploads anything. Hashes in the downloaded manifest alone are
   not treated as approval.

| Target | Required approval inputs |
| --- | --- |
| `build` | None |
| `pypi` | `expected_wheel_sha256`, `expected_sdist_sha256` |
| `npm` | `expected_tgz_sha256` |

Hashes must be exactly 64 lowercase hexadecimal characters. These requirements
are conditional: GitHub's dispatch form cannot make an input required only for
one target, so the workflow validates them before checkout and again in the
publish job. Dispatch permissions remain the authority boundary; hash matching
is not proof that an operator reviewed or approved the bytes.

For npm, dispatch the workflow itself at the same commit selected by `ref`.
The workflow verifies this so npm's provenance identifies the source actually
built, rather than a different workflow-branch commit.

## Registry setup

For PyPI's pending trusted publisher, register project `flattrace`, owner
`kyraneth`, repository `flattrace`, workflow filename `release.yml` and
environment `pypi`. The publish job has only `id-token: write` and runs
`uv publish --trusted-publishing always` on the approved wheel and sdist. It does
not check out or execute release source. uv requires OIDC rather than falling
back to a stored publishing token. This workflow does not generate PyPI
attestations; trusted publishing and artifact attestations are different features.

Publish npm version 0.1.0 manually first. npm cannot create the initial package
through trusted publishing. For later versions, configure the existing package's
trusted publisher with owner `kyraneth`, repository `flattrace`, workflow filename
`release.yml` and environment `npm`, permitting direct `npm publish`. The package
repository URL must continue to match this GitHub repository. The workflow uses
GitHub-hosted runners, Node 24.18.0 with its bundled npm 11.16.0, and
`npm publish <tgz> --provenance --access public`. npm requires Node >=22.14.0 and
npm >=11.5.1. Lifecycle scripts are disabled in the publish job. There is no
checkout, dependency installation or package build in that privileged job.

See [npm's trusted publisher documentation](https://docs.npmjs.com/trusted-publishers/),
[PyPI's publishing guide](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
and [uv's publishing guide](https://docs.astral.sh/uv/guides/package/).

## Reproducible local build

Use uv 0.9.27, Python 3.13.11, Node 24.18.0 with npm 11.16.0, and Rust 1.94.1,
as in the workflow. From a clean checkout of the selected commit:

```sh
umask 002
export SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)"
export TZ=UTC PYTHONDONTWRITEBYTECODE=1
uv build --python 3.13.11 --no-sources --out-dir dist/python
npm --prefix typescript ci --ignore-scripts
npm --prefix typescript run build
mkdir -p dist/npm
(cd typescript && npm pack --ignore-scripts --pack-destination ../dist/npm)
(cd dist && sha256sum python/*.whl python/*.tar.gz npm/*.tgz)
```

The Python backend is exactly pinned to `uv_build==0.9.27` in `pyproject.toml`.
It writes canonical ownership (uid/gid zero and empty owner names) and fixed
archive timestamps itself; no post-build archive rewriting is performed.
`SOURCE_DATE_EPOCH` records the source commit time for the build environment;
this backend's fixed timestamps do not depend on the wall clock or checkout
mtime. Python distributions include only the Python module, metadata, README
and license. npm dependency versions and integrity hashes come from
`typescript/package-lock.json`; packing happens after the explicit build with
implicit lifecycle scripts disabled.

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
