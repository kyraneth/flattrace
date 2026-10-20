# steptrace v1

Accurate offline records for performance, bugs, benchmarks, hill-climbing and
RL—not live monitoring. One UTF-8 JSON object per appended, flushed line;
each run owns a fresh file. Flush is not crash persistence. No collector/sampling.

| Field | Meaning |
|---|---|
| `v` | `1` |
| `seq` | Per-writer line counter from 0; emitted by new writers, optional for legacy v1 |
| `tool`, `tool_version`, `run` | Tool name/version and process-run ID |
| `trace` | Nonzero lowercase 32-hex W3C trace ID |
| `span`, `parent` | Nonzero lowercase 16-hex span IDs; parent null at root |
| `event`, `state` | Static event name; `start`, `end` or `point` |
| `t` | RFC3339 UTC, canonical `Z`; wall time for correlation, not duration |
| `took_ms`, `outcome` | Ends only: local elapsed milliseconds (null if unknown), outcome string |
| `data` | Tool payload object |
| `error` | Optional extensible object requiring string `kind` and `message` |

First line: `run` start with `data.argv` (sanitized strings; empty unless supplied),
`host`, `version`; new writers also emit optional `pid`. Recommended `data.env`
records known machine/build/
agent/harness/model facts using the [small vocabulary](docs/environment.md).
Unknown is null; omitted optional keys mean the tool does not report that fact.
Never scrape the whole environment. Model/effort changes belong on turn/model spans.
Last line: matching `run` end. Every start has one matching event/span/parent end;
missing ends are unfinished, never success. Async children may outlive parents,
but close/finish refuses active local spans. Points use an active span: a late
point is recorded on an active run with `data.late_span` and a note; after run
close it is ignored, never reopened. No crash/exit ends are invented.

`seq` detects gaps/reordering within a run, not total order across processes;
Files from earlier v1 writers (without `seq`) remain readable; no new envelope/run field is required. Across files use parentage/links, not
wall-time sorting. `took_ms` starts **after** the start write and ends before the
end write; nested instrumentation/waits still cost time. It measures residence,
not CPU work; never sum nested/overlapping durations or subtract wall timestamps.
Python `t` has microsecond, Rust nanosecond, Node Date millisecond representation;
none promises clock accuracy at that resolution. Non-UTF-8 paths are lossy text.

## Activation, ownership and streaming

Nonempty `STEPTRACE_DIR` enables tracing; unset/empty means no trace I/O.
Applications choose whether to set it. Create the directory if needed. Names `YYYYMMDDTHHMMSS.sssZ-tool-run.jsonl` sort by UTC start to millisecond
precision (shared Node resolution), then lowercase-slug tool/run. Exclusive
creation retries a fresh random run ID on collision; never overwrite/append.
An unavailable directory or later sink I/O failure warns once per process on
stderr, disables that sink and continues product work. Explicit-path helpers
are strict. steptrace deletes nothing; cleanup is the operator's choice.
Absence of a trace is not evidence of zero operations.

`STEPTRACE_TRACEPARENT` carries W3C v00 `00-<trace>-<span>-<flags>`
(32/16/2 lowercase hex digits). A child reuses trace, parents its run to that
span, and inherits `STEPTRACE_DIR`; flags never filter local events. Invalid
context is an error. After fork create a fresh writer with propagated context;
never reuse an inherited writer (pid mismatch disables it). Python calls must
be serialized by their owner; Rust requires exclusive `&mut` access; Node owns
one writer on one event loop, not shared between workers. No shared-file logging.

The file is already a stream (`tail -f`). A separate optional live sender could
tail `STEPTRACE_DIR`, sending export-cleaned lines per tool and opt-in with an
in-memory placeholder map for its lifetime. It must wait for complete lines;
an incomplete tail may be still being written or crashed. Not part of core v1.
Per-run files plus `check` and `jq` filtering keep long sessions inspectable;
there is no rotation, retention limit or index machinery.

## Learning conventions (all in data)

Optional run-start IDs: `task`, `variant`, `episode` (stable when replaying the
same attempt), `attempt` (intentional repeat ordinal), `seed` (integer/string or
null), `task_hash`, `config_hash` (algorithm-prefixed digests, null if unknown).
The **comparability block** is this subset of run data, not a second copy:
`task`, `variant`, `seed`, `task_hash`, `config_hash`, `env`. It is required only
for benchmark/RL comparability claims; hash inputs/meaning belong to the tool.
Missing/unknown identities remain evidence, not certified comparable samples.
Dedup re-exports on `(trace, run)`; intentional executions get new run IDs.
A retry gets a new span and `data.retry_of` (prior span ID); optional
`data.links: [{relation, trace, span}]` expresses async completion/fan-in/other
causality. These are data conventions, never envelope links or replay authority.

A grader writes `verdict` point data `{trace, run, name, correct, score, reward,
reason, grader}`: target IDs, score name, boolean correctness, numeric score and
reward (each null if unknown), explanation, `{name, version, kind}` grader
identity (`code`, `model`, `human`, or tool extension). Optional target `span`
enables step grading; checker-run envelope IDs are not target IDs. Multiple
grades remain observations, never implicit last-wins. Recording end vs missing
end is terminated vs truncated; error/cancelled ends are not Gymnasium MDP
success claims. Only verdicts assert correctness/reward. Exclude unfinished,
ungraded, cancelled/unknown runs from reward claims; null is not zero.
[The learning example](examples/learning.py) joins these facts without an optimizer.

Event names contain no dynamic IDs/paths; values belong in data. Core outcomes
have fixed meanings: `ok` completed, `error` failed, `refused` not admitted,
`cancelled` externally stopped, `unknown` unresolved; extensions are allowed.
Prefer stable `error.kind` plus structured context over sensitive error prose.
Inline useful facts/summaries; bulky artifacts may use `{sha256, bytes, path}`
references in data, with no automatic copying, blob store or attachment machinery.
Non-finite numbers become null plus `data.notes` entries; generated notes are
strings, with any existing non-array notes preserved as one array entry.
Ordinary unknown nulls have no added note. Rust callers use `set_number` before Value conversion:
serde_json turns non-finite numbers into null before the writer sees them.

Each tool owns `steptrace-events.json`: `tool`, `events` mapping names to
`kind` (`span`/`point`) and `fields` descriptions with units, optional global
`required` names and optional `commands` mapping command IDs to required arrays.
`check --events FILE TRACE...` pairs spans, rejects undeclared names/kinds and
checks requirements **per run**. With `commands`, select via run `data.command`
or CLI `--command ID`; legacy writers need no new payload field.
Tool tests own payload meaning and command/branch coverage, not a second schema.
[Oxblend 1.3.0's event list](examples/oxblend/steptrace-events.json) accompanies
[real exported examples](examples/oxblend/README.md).
Analysis can derive disposable paired-span rows (start, duration, outcome,
unfinished) keyed by `(trace, run, span)`; raw lines remain the only record.

## Sharing and evolution

Never supply credentials: tools sanitize argv/custom secret flags before capture;
writers also mask common token/password/key options. `steptrace export` is the
single redaction route for report-a-bug and per-tool opt-in upload. It substitutes
paths/hosts/user names consistently and masks credential-looking key/value text,
Authorization headers and URL credentials. It is **not a secret detector**:
supply ambiguous names explicitly and review paths, hosts, users and error text.
Only that reviewed export bundle is sent, never raw traces/assets; upload is a
separate per-tool opt-in. No uploader/server. Originals are untouched. Malformed
interior lines refuse export; a torn final line is omitted with a numbered
`.tail.txt` note, never copied unredacted. Unfinished evidence remains unfinished.

Additive fields stay v1; readers ignore unknown fields.
Breaking changes bump `v`.
