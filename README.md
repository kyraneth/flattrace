# flattrace

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/kyraneth/flattrace/main/assets/logo/flattrace-wordmark-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/kyraneth/flattrace/main/assets/logo/flattrace-wordmark-light.svg">
  <img alt="flattrace" src="https://raw.githubusercontent.com/kyraneth/flattrace/main/assets/logo/flattrace-wordmark-light.svg" width="444">
</picture>

**Flat file, deep trace.** flattrace records what a tool did as small, local JSONL events: nested steps, outcomes, errors and measured durations, even across child processes. Python, Rust and TypeScript writers share one v1 envelope. Check traces, find slow steps and redact a bundle for a bug report—all offline, without a collector, server or account. Code, docs and examples are [MIT licensed](LICENSE).

## Install

```sh
pip install flattrace                 # Python writer + flattrace CLI (+ reader engine on supported platforms)
cargo add flattrace serde_json        # Rust writer (Linux)
npm install flattrace                 # Node writer + TypeScript declarations (+ reader engine where bundled)
```

Packages: [PyPI](https://pypi.org/project/flattrace/),
[crates.io](https://crates.io/crates/flattrace) and
[npm](https://www.npmjs.com/package/flattrace). The 0.2.0 PyPI release ships
`cp310-abi3` wheels (manylinux2014 x86_64, macOS arm64) carrying the compiled
reader engine, plus a pure `py3-none-any` fallback where the writer and
check/export/stats keep working and the new `flattrace.reader` functions
raise `EngineUnavailable`. The npm package keeps the writer as its default
export and ships both native bindings; `flattrace/reader` loads the running
platform's binary and throws `EngineUnavailable` elsewhere. To work from a
checkout instead:
`python3 -m pip install .`; `flattrace = { path = "../flattrace/crates/flattrace" }`
in Cargo.toml; or `npm --prefix typescript install && npm --prefix typescript run build`
then `npm install ./typescript`. Rust also needs `serde_json`.
Python requires 3.10+, Node requires 22.18+, and Rust targets Linux.

## Quickstart

Each ten-line example writes a fresh file in the current directory; remove or
rename an existing demo file before rerunning. Explicit-path writers are strict;
use the environment-based API described below in production.

### Python

Save as `demo.py`, then `python3 demo.py`:

```python
from flattrace import Writer

w = Writer("python.jsonl", "demo", "0.1.0")
span = w.start("calculate", {"items": 3})
result = sum([1, 2, 3])
w.point("result", {"value": result}, span=span)
w.end(span, "ok")
w.close()
print(result)
print("Wrote python.jsonl")
```

### Rust

After `cargo new demo` and adding the dependencies above, replace `src/main.rs`
with this and run `cargo run` (Linux):

```rust
use flattrace::Writer;
use serde_json::json;
fn main() -> std::io::Result<()> {
    let mut w = Writer::create(std::path::Path::new("rust.jsonl"), "demo", "0.1.0", vec![], "demo")?;
    let span = w.start("calculate", json!({"items": 3}), None)?;
    let result: i32 = [1, 2, 3].iter().sum();
    w.point("result", json!({"value": result}), Some(&span), None)?;
    w.end(&span, "ok", json!({}), None)?;
    w.finish("ok")?; println!("{result}"); println!("Wrote rust.jsonl"); Ok(())
}
```

### TypeScript / JavaScript

Save as `demo.mjs`, then `node demo.mjs`. The same code works in TypeScript:

```js
import { Writer } from 'flattrace';

const w = new Writer('node.jsonl', 'demo', '0.1.0');
const span = w.start('calculate', { items: 3 });
const result = [1, 2, 3].reduce((a, b) => a + b, 0);
w.point('result', { value: result }, span);
w.end(span, 'ok');
w.close();
console.log(result);
console.log('Wrote node.jsonl');
```

## Check and analyse

The Python package supplies the CLI for **all three** writers:

```sh
flattrace check python.jsonl rust.jsonl node.jsonl
flattrace stats python.jsonl rust.jsonl node.jsonl
flattrace stats traces/ --json          # recursive; also reads .jsonl.gz
flattrace check --events flattrace-events.json python.jsonl
```

A deterministic synthetic fixture illustrates real CLI output (not performance data):

```sh
flattrace check tests/fixtures/legacy/1.jsonl
```

```text
tests/fixtures/legacy/1.jsonl: 5 valid events
```

```sh
flattrace stats tests/fixtures/legacy/1.jsonl
```

```text
Runs: 1  files: 1  duplicates excluded: 0
Status: {"ok": 1}
Levels (runs): {"unknown": 1}
Span durations across runs (ms; inclusive totals are NOT additive):
  file.open: n=1 p50=0 p95=0 max=0 total=0
    self: n=1 p50=0 p95=0 max=0 total=0
  run: n=1 p50=0 p95=0 max=0 total=0
    self: n=1 p50=0 p95=0 max=0 total=0
Errors by name (explicit error/outcome/severity; coalesced count): {}
Warnings by name (explicit data.severity=warn/warning only; otherwise unknown): {}
Slowest runs, with dominant spans (inclusive ms; unknown durations last):
  compat 00000000000000000000000000000002 ok 0 ms (tests/fixtures/legacy/1.jsonl)
    file.open: n=1 p50=0 p95=0 max=0 total=0
      self: n=1 p50=0 p95=0 max=0 total=0
    run: n=1 p50=0 p95=0 max=0 total=0
      self: n=1 p50=0 p95=0 max=0 total=0
Self = parent residence minus sequential, contained LOCAL direct-child residence; not CPU time. Cross-process children are not subtracted. Overlap/async/unknown => unknown, never wall-time subtraction.
```

Exit 0 means valid, complete evidence; 1 means validation issues or unfinished
work; 2 means invocation or I/O failure. Missing ends stay unfinished. Inclusive
nested durations must not be added as elapsed time; `self_ms` is unknown when
local overlap or incomplete evidence prevents safe attribution.

## Read at scale

The Rust reader engine (`crates/flattrace-read`) replays the corpus once,
spills span records and exact-quantile samples to a temporary SQLite file and
answers a frozen function set documented in [docs/reader.md](docs/reader.md):
`check`, `spans` (paired rows with filters), `digest` (compact table, error
census, exact pointers, coverage statement), `group`, `tree` (traceparent
across processes), `compare` and `overlap` (stall windows vs spans). Filters
cover time windows, trace/run, event/tool names, agent, model, outcome,
session ID and arbitrary payload field paths. Unknown never becomes 0;
unfinished stays unfinished; durations come only from `took_ms`.

```sh
cargo run --manifest-path crates/flattrace-read/Cargo.toml -- check TRACE.jsonl
flattrace-read stats traces/ --json
flattrace-read digest traces/ --filter '{"event": "tool.call"}'
```

```python
from flattrace import reader
table = reader.digest(["traces/"])  # EngineUnavailable without the abi3 wheel
```

```js
import { digest } from 'flattrace/reader';
const table = digest(['traces/']); // EngineUnavailable on unsupported platforms
```

The engine and its Python/Node bindings assert the same golden outputs as
the frozen conformance corpus (`tests/conformance/`); see
[bench/READER.md](bench/READER.md) for the measured comparison with
`flattrace stats`.

## Instrument a real tool

Use `Writer.from_env` / `Writer::from_env` / `Writer.fromEnv`:
`FLATTRACE_DIR=traces` enables a new file per run, unset/empty disables tracing.
Environment sink failures warn once and disable the sink without stopping product
work; structural API errors still surface. `FLATTRACE_LEVEL=0` disables I/O,
level 1 is actions/decisions/aggregates, and level 2+ adds per-item detail.
**The tool gates detail; writers do not filter your calls.**

Own a `flattrace-events.json` with static names, span/point kinds and required
events per command. End every local span, then close/finish the run—never end
the root directly. Propagate the enclosing span's traceparent and directory to
children, each with its own writer/file. See the [agent skill](skills/flattrace/SKILL.md),
[nested process example](examples/nested/parent.py), [examples](examples/README.md)
and [API/analysis reference](docs/reference.md).

## Why flattrace?

- **Versus OpenTelemetry:** much smaller operational surface for studying local
  tools after they run. W3C-width IDs and parentage are familiar, but this is not
  OTLP, an OTel SDK, distributed monitoring, or an automatic OTel exporter.
- **Versus ad-hoc JSON logs:** one shared envelope adds pairing, parentage,
  sequence checks, durations and unfinished-run detection. It is not a general
  log framework or a guarantee of crash persistence.

## Share carefully

```sh
flattrace export python.jsonl rust.jsonl node.jsonl --out reviewed-share
```

Export consistently replaces paths/hosts/users and masks common credential
patterns. It is **not a secret detector**: never log credentials; review the
bundle and supply `--path`, `--host`, `--user` for ambiguous names before sharing.
Only share reviewed exports, never raw traces. Originals remain untouched.

## Develop and extend

[SPEC.md](SPEC.md) is normative; [JSON Schema](schema/event.schema.json) describes
the envelope, not complete stream validation. See [CONTRIBUTING.md](CONTRIBUTING.md)
for all suites, [AGENTS.md](AGENTS.md) for repository invariants,
[CHANGELOG.md](CHANGELOG.md), [ROADMAP.md](ROADMAP.md), and optional
[learning conventions](docs/learning.md). No uploader, retention service or live
collector is included.
