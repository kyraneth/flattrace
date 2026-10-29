# flattrace v1

Accurate offline records for performance, bugs, benchmarks, hill-climbing and
RL—not live monitoring. Flat file, deep trace: spans nest across processes
through `span`/`parent`. There is no collector or sampling.

## Normative contract

MUST, MUST NOT, SHOULD and MAY below express requirements, recommendations and
permissions. The [rationale and limits](#rationale-and-limits-non-normative) are
non-normative. [Learning conventions](docs/learning.md) describe optional payload
usage; they do not change the envelope or checker behavior.

### Line format and compatibility

- A writer MUST create a fresh file for each run and MUST append one UTF-8 JSON
  object per line, flushing each line. Writers MUST write plain JSONL. Readers
  MUST also accept gzip archives named `<name>.jsonl.gz` with identical JSONL
  content after decompression.
- The envelope MUST have the following fields and meanings. Readers MUST ignore
  additive unknown fields; breaking changes MUST bump `v`.

| Field | Requirement / meaning |
|---|---|
| `v` | MUST be `1` |
| `seq` | New writers MUST emit a per-writer integer line counter starting at 0 and increasing by 1; legacy v1 MAY omit it |
| `tool`, `tool_version`, `run` | MUST identify the tool name/version and process run |
| `trace` | MUST be a nonzero lowercase 32-hex W3C trace ID |
| `span`, `parent` | MUST be nonzero lowercase 16-hex span IDs; `parent` MAY be null at an unparented root |
| `event`, `state` | MUST be a static event name and `start`, `end` or `point` |
| `t` | MUST be RFC3339 UTC; readers MUST accept both `Z` and `+00:00`, writers SHOULD use `Z` |
| `took_ms`, `outcome` | MUST occur only on ends: local elapsed milliseconds (null if unknown) and an outcome string |
| `data` | MUST be a JSON payload object |
| `error` | MAY be an extensible object; if present MUST contain string `kind` and `message` |

- The rename MUST preserve the v1 envelope and previously valid v1 files;
  `seq`, `pid` and new payload fields MUST NOT be required of legacy files.
  Only the environment names specified here are supported; there are no
  previous-name aliases.

### Run and span lifecycle

- The first line MUST be a `run` start with `data.argv` (sanitized strings,
  empty unless supplied), `host` and `version`. New writers MAY also emit `pid`.
  The last line of a complete run MUST be its matching `run` end.
- Each start in a complete run MUST have exactly one matching event/span/parent
  end. Missing ends MUST remain unfinished, never inferred success. Writers
  MUST NOT invent crash, exit or drop ends.
- A point MUST reuse **both the enclosing active span's ID and its parent**;
  it MUST NOT allocate a new span or set `parent` to the enclosing span's ID.
  A late point MUST instead use the active run span and its parent, recording
  the requested span in `data.late_span` plus a note. After run close it MUST
  be ignored, never reopen the run.
- Async children MAY outlive nonroot parents, including locally. The run MUST
  NOT end while any local nonroot span remains active. Only `close`/`finish`
  MAY end the root. A direct `end(root)` MUST be refused: explicit-path writers
  MUST report an API error; environment-mode writers MUST warn and return
  without raising (Rust returns `Ok(())`), leaving the root active.
  `close`/`finish` with active local spans MUST remain an API error.
  `close`/`finish` MAY carry a run-end `data` payload (for example run totals),
  normalized like any other payload; omitted means `{}`.
- `took_ms` MUST measure local elapsed residence, starting after the start write
  and ending before the end write. Consumers MUST NOT derive durations from
  wall timestamps or sum nested/overlapping durations as elapsed work.
- Where `seq` is present, the checker MUST check its continuity and order within
  a run. Across files consumers SHOULD use parentage/links, not wall-time sorting.

### Activation, ownership and failures

- Nonempty `FLATTRACE_DIR` MUST enable a fresh run file; unset/empty MUST mean
  no trace I/O. The writer MUST create the directory if needed.
- Environment filenames MUST have the shape
  `YYYYMMDDTHHMMSS.sssZ-tool-run.jsonl`, with UTC start time to milliseconds and
  lowercase-slug tool/run. Creation MUST be exclusive, retrying a fresh random
  run ID on collision, never overwriting or appending an existing file.
- An unavailable directory or later environment sink I/O failure MUST warn once
  per process on stderr, disable that sink and continue product work. A disabled
  writer MUST NOT resume writes to recover missing records. Explicit-path
  helpers MUST retain I/O errors. Writers MUST NOT delete traces.
- `FLATTRACE_TRACEPARENT` MUST carry W3C v00
  `00-<trace>-<span>-<flags>` (32/16/2 lowercase hex digits). A child MUST reuse
  the trace, parent its run to that span and inherit `FLATTRACE_DIR`; flags MUST
  NOT filter local events. Invalid context MUST remain an error.
- After fork the child MUST create a fresh writer with propagated context,
  never reuse an inherited writer; a pid mismatch MUST disable inherited writes.
  Python calls MUST be serialized by their owner; Rust MUST require exclusive
  `&mut` access; Node MUST own one writer on one event loop, not share it
  between workers. Writers MUST NOT share files across processes.

### Payload capture and API boundaries

- Writers MUST normalize payloads to JSON-safe snapshots without changing caller
  objects. Unserialisable payload values MUST degrade to safe strings/type
  placeholders plus a `data.notes` entry, and writing MUST continue. Python and
  TypeScript normalization MUST handle unsupported nested types and cycles.
  A nonobject top-level payload MUST become
  `{"value": <normalized original>, "notes": [...]}` with a note about wrapping.
- Non-finite numbers MUST become null plus `data.notes` entries. Generated notes
  MUST be strings; an existing non-array `notes` value MUST be preserved as one
  array entry. Ordinary unknown nulls MUST NOT gain a replacement note.
- Rust payloads MUST enter as `serde_json::Value`; unsupported runtime types,
  cycles and unpaired surrogates cannot cross that type boundary. No general
  serialization helper is part of this API. Rust callers SHOULD use
  `set_number` before Value conversion to retain a non-finite replacement note.
- Payload normalization MUST NOT turn structural API mistakes into success:
  non-string tool/version/event names, reserved `run` events, inactive
  spans/parents, malformed error structure and invalid outcome types MUST
  remain API errors, except the environment-mode
  direct `end(root)` refusal above. Rust outcomes MUST be `&str`.
- Full event/stream validation MUST belong to the checker, not a per-line writer
  validation pass. Event names SHOULD contain no dynamic IDs/paths; values
  SHOULD go in data. Core outcome strings MUST keep these meanings: `ok`
  completed, `error` failed, `refused` not admitted, `cancelled` externally
  stopped, `unknown` unresolved. Extensions MAY be used.
- Tools SHOULD prefer stable `error.kind` and structured context over sensitive
  error prose, inline useful facts/summaries, and use optional
  `{sha256, bytes, path}` references for bulky artifacts. There is no automatic
  copying, blob store or attachment machinery.
- Tools SHOULD record known machine/build/agent/harness/model facts in run-start
  `data.env` using the [small vocabulary](docs/reference.md#environment-and-build-provenance).
  Reported-but-unknown facts MUST be null; omitted optional keys mean not reported.
  Tools MUST NOT scrape the whole environment. Model/effort changes SHOULD be
  recorded on turn/model spans rather than only the initial header.

### Tool event files and checking

- Each tool MUST own `flattrace-events.json`, containing `tool` and an `events`
  mapping from names to `kind` (`span`/`point`) and `fields` descriptions with
  units. **Every event file MUST declare `run` with `kind: "span"`.**
- An event file MAY include global `required` names and a `commands` mapping
  command IDs to required arrays. With `commands`, selection MUST use run
  `data.command` or CLI `--command ID`; legacy writers need no new payload field.
- `check --events FILE TRACE...` MUST pair spans, reject undeclared names/kinds
  and check requirements per run, never satisfy them using another run.
  `--events` MAY be repeated; the run is validated against the union of the
  supplied vocabularies (see the composition truth table in
  [reference](docs/reference.md)). A run MAY contain events owned by several
  tools; it is validated against the union of their vocabularies.
  Tool tests MUST own payload meaning and command/branch coverage; field
  descriptions MUST NOT imply an executable payload schema.
- Every event file MUST declare `run` with `kind: "span"`. Vocabularies that
  are composed in one run copy the reserved `run` declaration published with
  the checker
  ([schema/run-declaration.json](schema/run-declaration.json), repeated in
  [reference](docs/reference.md)): owner-specific measurement prose belongs
  on the events that own those measurements, never in that shared block, so
  composed vocabularies agree on `run` instead of colliding on it.

### Unicode policy

- Writers MUST emit only valid Unicode scalar strings. Lone UTF-16
  surrogates MUST be replaced with U+FFFD plus a `data.notes` entry, keeping
  a valid surrogate pair as its character; when two object keys collide after
  replacement the writer MUST keep the first and note the collision.
  Writing continues. JSON escapes of lone surrogates in legacy lines are
  parsed leniently by readers and reported by every checker as the same
  warning-level notice, never silently dropped.

### Detail levels

- `FLATTRACE_LEVEL` MUST control how much a run records and MUST be inherited
  like `FLATTRACE_DIR`. Unset/empty MUST mean `1`; `0` MUST mean no trace I/O even
  with `FLATTRACE_DIR` set. Writers MUST trim Unicode `White_Space` plus
  U+001C–U+001F (Python's whitespace set), not U+FEFF. Only ASCII digits `0`–`9`
  MUST then be parsed as a level; other values MUST fall back to `1` with a run note.
  Values outside the writer's exact native integer representation MUST use that
  fallback (Rust `u64`, TypeScript safe integer, Python arbitrary precision).
  Python's configured integer-string conversion restrictions MUST also use the
  fallback. Environment writers MUST record the effective `data.level` on
  run start; tools, not writers, MUST decide what to emit at each level.
- Level **1** SHOULD be rich without noise: action/command spans, outcomes and
  decisions, state transitions, aggregates instead of high-frequency streams,
  and full known-facts `data.env`. Level **2+** SHOULD additionally record raw
  events (each poll, redraw, property write); tools MAY refine meanings above 2.
- Aggregates SHOULD use ordinary events with these data conventions:

| Record | Shape |
|---|---|
| Coalesced repeats | Any point/span with `count` (integer ≥1: represented occurrences) and optional `first_t`/`last_t`. An edit adds `target`, `key`, `old` (before first), `new` (after last). |
| Transition | Point with `from` and `to`; emit only on change, never per poll. |
| Window summary | Span bounding the window (e.g. `playback.window`); end carries `stats: {metric: {unit, n, p50, p95, max}}` plus counters such as `dropped_frames`. Percentiles are over the window's samples. |
| Deduplicated error | Point carrying `error` `{kind, message}`, `where` (`file:line`), `count`, `first_t`, `last_t`; one per distinct `kind` + `where` per run. |

- An event MAY declare `level` in `flattrace-events.json` (default `1`).
  `check --events` MUST report higher-level events in lower-level runs and
  MUST NOT require events above the run's level.

### Sharing and evolution

- Tools MUST NOT supply credentials. They MUST sanitize argv/custom secret
  flags before capture; writers MUST also mask common token/password/key
  options. `flattrace export` MUST be the single redaction route for report-a-bug
  and per-tool opt-in upload, substituting paths/hosts/user names consistently
  and masking credential-looking key/value text, Authorization headers and URL
  credentials. Users MUST review paths, hosts, users and error text and supply
  ambiguous names explicitly before sharing.
- Only a reviewed export bundle MAY be sent, never raw traces/assets; upload
  MUST be a separate per-tool opt-in. Export MUST leave originals untouched,
  refuse malformed interior lines, and omit a torn final line with a numbered
  `.tail.txt` note, never copy its raw bytes unredacted. Unfinished evidence
  MUST remain unfinished.

## Rationale and limits (non-normative)

`seq` exposes removed interior records and reordered lines when counters no
longer follow the expected sequence. It cannot prove durability or completeness:
flush is not crash persistence, an absent trailing suffix may leave no visible
gap, and a failed write may never reach disk. Environment sink failure disables
the writer rather than recovering missing writes, so there may be no later
counter exposing the loss. Counters do not establish cross-process order or
cryptographic integrity. Absence of a trace is not evidence of zero operations.

`t` is wall time for correlation, not duration. Python timestamps represent
microseconds, Rust nanoseconds and Node Date milliseconds; none promises clock
accuracy at that resolution. `took_ms` includes waits and nested instrumentation,
not just CPU work. Non-UTF-8 paths are lossy text. serde_json turns non-finite
numbers into indistinguishable null before the writer sees them unless captured
with `set_number` first.

The file is already a stream (`tail -f`). A separate optional live sender could
tail `FLATTRACE_DIR`, sending export-cleaned lines per tool and opt-in with an
in-memory placeholder map for its lifetime. Such a sender would wait for complete
lines; an incomplete tail may still be being written or may have crashed. This
is outside core v1. Per-run files plus `check`/`jq` keep sessions inspectable;
there is no rotation, retention limit or index machinery. Cleanup is the
operator's choice. Export is not a secret detector and core supplies no uploader
or server.

[Oxblend's public-doc draft](examples/oxblend/flattrace-events.json) illustrates a tool-owned event list; it is not an integration guarantee.
Analysis can derive disposable paired-span rows (start, duration, outcome,
unfinished) keyed by `(trace, run, span)`; raw lines remain the only record.
