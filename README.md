# flattrace

Small, local JSONL traces for understanding tools after they run. Record nested
steps, timings, outcomes and explicit benchmark verdicts; join processes through
W3C traceparent IDs. Unfinished steps stay unfinished—even after SIGKILL.
No collector, upload service, retention policy or live-monitoring dependency.
Code and documentation are [MIT](LICENSE).

## Does "flat" mean no depth?

No. **Flat file, deep trace.** *Flat* describes the file: every line is one
small, self-contained JSON event, so a writer just appends, you can `grep` or
stream it, and a crash costs at most the last line. The trace itself is a tree
as deep as you like, even across processes: each step carries its own `span`
and its `parent`, and a child process joins the caller's trace through
`FLATTRACE_TRACEPARENT`.

A host tool runs a `preflight` step that launches a separate `my-tool` process:

```
my-addon  run
└─ preflight
   └─ my-tool  run                (child process, joined via FLATTRACE_TRACEPARENT)
      └─ summary
         ├─ file.open
         ├─ file.index
         └─ file.schema
```

On disk that is two ordinary files of flat lines (abridged here: `t`, `trace`,
`run`, `seq` and `data` omitted, IDs shortened):

```jsonl
{"v":1,"tool":"my-addon","event":"preflight","state":"start","span":"e390…","parent":"df4e…"}
{"v":1,"tool":"my-tool","event":"run","state":"start","span":"fc01…","parent":"e390…"}
{"v":1,"tool":"my-tool","event":"summary","state":"start","span":"8b97…","parent":"fc01…"}
{"v":1,"tool":"my-tool","event":"file.open","state":"start","span":"10ee…","parent":"8b97…"}
{"v":1,"tool":"my-tool","event":"file.open","state":"end","span":"10ee…","parent":"8b97…","outcome":"ok"}
{"v":1,"tool":"my-tool","event":"summary","state":"end","span":"8b97…","parent":"fc01…","outcome":"ok"}
```

Readers rebuild the tree by joining each event's `parent` to a `span`: the same
model as OpenTelemetry spans, kept as plain lines in a file instead of being
sent to a server. Reproduce it with
`FLATTRACE_DIR=out PYTHONPATH=. python3 examples/nested/parent.py`, then
`python3 -m flattrace check out/*.jsonl`.

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
