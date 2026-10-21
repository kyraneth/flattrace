# flattrace

Small, local JSONL traces for understanding tools after they run. Record nested
steps, timings, outcomes and explicit benchmark verdicts; join processes through
W3C traceparent IDs. Unfinished steps stay unfinished—even after SIGKILL.
No collector, upload service, retention policy or live-monitoring dependency.
Code and documentation are [MIT](LICENSE).

## Does "flat" mean no depth?

No. **Flat file, deep trace.** *Flat* describes the file: every line is one
small, self-contained JSON event, so a writer just appends and you can `grep`
or stream it. Flush is not crash persistence: a crash may lose more than the
last line. The trace itself is a tree
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

The writer also depends on regex for credential masking.

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

All writers flush each line, require explicit ends, and invent nothing on drop,
exit or kill. Asynchronous children can outlive their launching span; end all
local spans before closing the run. Writers are synchronous, not shared-file
multi-process loggers. No writer deletes files or uploads anything.
Calls must be serialized (Python owner, Rust `&mut`, Node event-loop owner).
A forked child must start a fresh writer; pid checks prevent inherited writes.
Points reuse both their enclosing span ID and that span's parent (not a new
span parented to the enclosing span). Late points on ended spans move to the
active run with its parent and a note; points after run close are ignored.
Only `close`/`finish` ends the root, and closing with active local spans is an
API error. Direct `end(root)` is refused: explicit-path mode reports an API
error; environment mode warns and returns without raising (Rust `Ok(())`),
leaving the run active.

Tools supply **sanitized** argv explicitly; absent argv is empty, never an
implicit process-argument scrape. Customize for tool-specific secret flags.
Writers additionally mask common token/password/secret/API-key/access-key/key
options. Unserialisable payload values degrade to safe strings/placeholders plus
notes and writing continues; payload normalization is not full event validation.
The checker retains full validation, not a per-line writer validation pass.
Structural API mistakes (non-string tool/version/event names, reserved `run`,
inactive spans/parents, malformed error objects and invalid outcome types) still
report errors, apart from the explicit
environment-mode `end(root)` exception above. They may leave unfinished evidence;
never invent successful ends to hide it. These helpers are not a secret detector;
credentials should never reach trace calls or error prose. Use `sanitize_argv` /
`sanitizeArgv` when useful. Omit optional errors using the native absence value:
Python `None`, Rust `None`, TypeScript `undefined`; JSON null is not an error object.

## Offline analysis

[Learning conventions](docs/learning.md) define optional comparability identities,
grades and hill-climbing/RL consumption, all in `data`. The
[learning join](examples/learning.py) demonstrates these conventions without
changing the format or certifying benchmark validity.

A reader may derive a disposable paired-span row keyed by `(trace, run, span)`:
start time, measured duration, outcome, unfinished flag and source pointers.
That is an analysis view, not a second recording format; no invented end or
exclusive CPU time. Async/overlapping residence times cannot safely be summed
or blindly subtracted. The streaming `stats` reader below derives these views without retaining raw
rows; raw JSONL remains the source of truth.

## Analysing traces

```sh
python3 -m flattrace stats examples/
python3 -m flattrace stats trace.jsonl archive.jsonl.gz --json
```

Files and recursively scanned directories may be mixed. Reports include run
counts/status, reported run-level distribution (legacy missing level is
`unknown`), exact nearest-rank p50/p95/max/total durations by name for each run
and the corpus, every run ordered slowest-first, and its dominant span names.
Unknown-duration runs sort last. There is no arbitrary top-N cutoff. Completed,
matched span durations are included even in unfinished runs, with issues and
unfinished counts visible. Empty/invalid files are reported separately, never
invented runs. Exit 0 means no input issues, 1 means invalid/unfinished evidence,
and 2 means invocation/I/O failure. Check and export are unchanged.

**Inclusive totals are sums of observations, not elapsed corpus time:** nested
or overlapping span names must not be added together. `self_ms` is a conservative
local unattributed-residence estimate: parent `took_ms` minus sequential, fully
contained direct local children. It includes instrumentation/waits, not just CPU
work. It is unknown for overlapping children, children that outlive a parent
(including affected ancestors), unknown child durations, or invalid/incomplete
run evidence. Cross-process children are not subtracted: v1 has no common
monotonic timeline for an exact interval union. Wall timestamps are never used
to derive durations. The report gives sample counts separately for known total
and self times; unknown is not zero.

Errors count records carrying `error`, `outcome=error`, or explicit
`data.severity=error`, once per record. Warnings count only explicit
`data.severity=warn`/`warning`; v1 has no standardized warning field, so empty
counts do **not** establish absence of warnings. Names/prose are never guessed.
A positive integer `data.count` expands an error/warning record's occurrences;
span-duration sample counts still count spans, not coalesced occurrences.

Inputs are deduplicated by `(trace, run)`, first copy encountered wins (directory
traversal is lexical). Duplicates are listed in JSON and still fully validated (bad duplicate evidence
also produces exit 1); only their duration/error/warning samples are excluded.
Do not mix divergent copies of the same run or use this view as an
integrity/comparability certificate.
The reader processes one line at a time. Active span frames and run/name summaries
remain in memory; completed samples and seen-span IDs go to a temporary SQLite
file, removed on exit. Exact quantiles use disk-backed sorting, not a retained
list of events or approximate histogram. Diagnostics are grouped by fixed issue
category with occurrence counts and the first source-line detail (`issues` plus
`issue_counts`), not retained once per bad line; raw inputs preserve every detail.
Temporary disk must accommodate the selected corpus's duration samples. Output includes source paths/IDs: it is local
analysis, **not** a redacted sharing export.

See [GAPS.md](GAPS.md) for remaining format/SPEC proposals.


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
[environment vocabulary](docs/environment.md), [learning conventions](docs/learning.md)
and [learning join](examples/README.md)
explain the contracts and their limits. Timings are residence time, not exclusive
CPU time; do not sum overlapping spans. IDs establish parentage, not timestamps.
Check/export load the selected files in memory; stats streams them. Counters
expose removed interior lines and reordering, not durability or every lost suffix.
No performance claim is implied by the example traces.

The [synthetic overhead harness](bench/README.md) measures paired writer workloads;
machine-specific retained reports are not bundled.

## Tests

```sh
python3 -m unittest
cargo test --manifest-path crates/flattrace/Cargo.toml --offline
node --test typescript/test/*.test.mjs
```

Rust needs an existing dependency cache for offline testing. All writers are
synchronous and single-process; serialize calls, create a fresh writer after
fork, and end local spans before close/finish. No ends are invented on drop.
