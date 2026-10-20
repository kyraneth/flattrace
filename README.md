# flattrace

Small, local JSONL traces for understanding tools after they run. Record nested
steps, timings, outcomes and explicit benchmark verdicts; join processes through
W3C traceparent IDs. Unfinished steps stay unfinished—even after SIGKILL.
No collector, upload service, retention policy or live-monitoring dependency.
Code and documentation are [MIT](LICENSE).

## Quick start

Use this checkout; registry packages are not published. Set `FLATTRACE_DIR` to a
local trace directory to enable environment writers. Unset or empty means off.
Each process owns a fresh file. Environment sink failures warn once and disable
recording without stopping product work; explicit-path constructors are strict.

### Python (standard library)

Run from the checkout, or add it to `PYTHONPATH`:

```python
from flattrace import Writer

w = Writer.from_env("example", "0.1.0", argv=["build"])
if w is not None:
    span = w.start("build")
    # Do work; propagate w.child_env(span) when launching a child process.
    w.end(span, "ok")  # Record the actual outcome, not an assumed success.
    w.close()
```

### Rust (Linux)

Add local dependencies to your application's `Cargo.toml`:

```toml
[dependencies]
flattrace = { path = "../flattrace/crates/flattrace" }
serde_json = "=1.0.150"
```

```rust
use serde_json::json;
use flattrace::Writer;

fn main() -> std::io::Result<()> {
    // Supply known hostname, or empty text if unavailable; do not guess.
    if let Some(mut w) = Writer::from_env("example", "0.1.0", vec!["build".into()], "", json!({}))? {
        let span = w.start("build", json!({}), None)?;
        // For a child: set FLATTRACE_TRACEPARENT from w.traceparent(Some(&span))?.
        w.end(&span, "ok", json!({}), None)?;
        w.finish("ok")?;
    }
    Ok(())
}
```

### TypeScript (Node >=22.18, native type stripping)

Run a `.ts` file in the checkout; no dependencies or compilation step required:

```ts
import { Writer } from './typescript/writer.ts';

const w = Writer.fromEnv('example', '0.1.0', { argv: ['build'] });
if (w) {
  const span = w.start('build');
  // For a child: spawn(..., { env: w.childEnv(span) }).
  w.end(span, 'ok');
  w.close();
}
```

## Check, export and inspect

Install from the checkout (`python3 -m pip install .`) for the console CLI:

```sh
flattrace check TRACE.jsonl
flattrace export TRACE.jsonl CHILD.jsonl --out reviewed-share
```

The equivalent checkout invocation is:

```sh
python3 -m flattrace check TRACE.jsonl
python3 -m flattrace check --events TOOL/flattrace-events.json TRACE.jsonl
python3 -m flattrace export TRACE.jsonl CHILD.jsonl --out reviewed-share
```

Export substitutes paths, hosts and users consistently, and masks common
credential-shaped text. Supply additional `--path`, `--host` and `--user` names
when needed. It is **not a secret detector**: review the whole bundle before
sharing; never supply credentials to writers. Originals remain untouched.
Exit 0 means valid complete input, 1 means validation issues/unfinished evidence
or an omitted torn final line, and 2 means invocation/I/O failure. Export refuses
interior corruption; an omitted final line gets a numbered `.tail.txt` note.

See [real Oxblend 1.3.0 exports](examples/oxblend/README.md) for summary, dependency
trace, complete UDIM pack, interrupted pack and Python-to-Rust nesting.
[The format specification](SPEC.md), [JSON Schema](schema/event.schema.json),
[environment vocabulary](docs/environment.md) and [learning join](examples/README.md)
explain the contracts and their limits. Timings are residence time, not exclusive
CPU time; do not sum overlapping spans. IDs establish parentage, not timestamps.
Readers currently load the selected files in memory. No performance claim is
implied by the example traces.

## Tests

```sh
python3 -m unittest
cargo test --manifest-path crates/flattrace/Cargo.toml --offline
node --test typescript/test/*.test.mjs
```

Rust needs an existing dependency cache for offline testing. All writers are
synchronous and single-process; serialize calls, create a fresh writer after
fork, and end local spans before close/finish. No ends are invented on drop.
