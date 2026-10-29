# Writer and analysis reference

## Writers and activation

Products use `FLATTRACE_DIR`: unset/empty is off; set enables a new per-run file.
Pass `FLATTRACE_TRACEPARENT` from the enclosing span to child tools.
An unavailable sink warns once on stderr and product work continues. Environment
writers disable a failing sink, including later write failures; explicit-path
constructors stay strict for tests and deliberate file creation.

Python (standard library, from this checkout):

```python
from flattrace import Writer
w = Writer.from_env("oxblend", "dev", argv=["summary", "scene.blend"],
                    data={"command": "summary", "task": "fps", "variant": "candidate"})
# Do product work regardless of whether w is None.
if w is not None:
    span = w.start("summary")
    # Launch a child with env=w.child_env(span), if needed.
    w.end(span, "ok")  # Use the actual outcome; errors are {kind, message}.
    w.close()
```

Rust (Linux, std + serde_json + regex for credential masking) uses `Writer::from_env(tool, version, argv, host,
data) -> io::Result<Option<Writer>>`. `None` means off/unavailable sink; malformed
context and structural API mistakes remain errors. `start`, `point`, `end`, `traceparent` and
`finish` mirror Python; use `root_span()` / `trace_id()` for IDs. Construct child
environments by inheriting `FLATTRACE_DIR` and setting `FLATTRACE_TRACEPARENT`
from `traceparent(Some(enclosing_span))`. `create` and `create_with_data` are
strict explicit-path constructors. Convert native paths with `to_string_lossy()`.
For floating-point capture, `set_number(&mut data, key, value)` writes a finite
number or null plus `data.notes`; use it before `json!`/Value conversion because
serde_json otherwise erases NaN/Infinity into indistinguishable null. Python
and TypeScript normalize snapshots directly, replacing unsupported nested types
and cycles with safe type placeholders plus notes; an ordinary unknown null
never gets a note. Nonobject top-level data becomes `{value: original,
notes: [...]}` with a wrapping note, using the normalized original value.
Rust accepts only `serde_json::Value` payloads and `&str` outcomes: unsupported
runtime types, cycles and unpaired surrogates cannot cross that payload boundary.
There is no new general serialization helper.

TypeScript for Node, with no runtime dependencies:

```ts
import { Writer } from 'flattrace';
const w = Writer.fromEnv('oxblend', 'dev', {
  argv: ['summary', 'scene.blend'], data: { command: 'summary' }
});
if (w) {
  const span = w.start('summary');
  // spawn(..., { env: w.childEnv(span) });
  w.end(span, 'ok');
  w.close();
}
```

The npm package ships built JavaScript and TypeScript declarations. Checkout tests run `.ts` directly on Node >=22.18.
`new Writer(path, tool, version, options)` is strict. Methods are `start`, `point`,
`end`, `traceparent`, `childEnv`, `close`; `run`, `root`, `trace`, `path` expose
IDs/file location. Decode native path Buffers with UTF-8 replacement text.
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

## Environment and build provenance

Recommended, not mandatory: put known facts in one `data.env` object on run
start. Claimed but unknown values are null; omitted keys mean this tool does
not report that fact. Do not dump
`process.env`, credentials or unrelated machine state. The dotted keys below
use OpenTelemetry resource names where meanings match; other keys are small
flattrace conventions, not a claim that OTel defines them.

| `env` key | Value |
|---|---|
| `host.name`, `host.arch` | Hostname (same fact as header host), architecture |
| `os.type`, `os.version` | OS family and reported OS version |
| `os.kernel.version` | Kernel release/version (flattrace convention) |
| `host.cpu.model.name` | CPU model name |
| `service.name`, `service.version` | Tool name/version (same facts as envelope) |
| `process.runtime.name`, `process.runtime.version` | Runtime, e.g. Python/Node, and version; null if unavailable/not applicable |
| `tool.build.commit`, `tool.binary.sha256` | Known build revision and binary SHA-256 (flattrace conventions) |
| `libraries` | Known dependency list of `{name, version}` (flattrace convention) |
| `libraries.lockfile.sha256` | Optional lockfile hash instead of a full list; null if unknown (flattrace convention) |
| `agent.name`, `agent.version` | Known agent identity/version (flattrace conventions) |
| `harness.name`, `harness.version` | Known execution harness identity/version (flattrace conventions) |
| `gen_ai.provider.name`, `gen_ai.request.model` | Requested provider and model ID |
| `model.requested_effort` | Requested thinking/effort setting, not proof of provider acceptance (flattrace convention) |

Tools fill only what they actually know; this vocabulary adds no machine-scan
or hashing requirement. The full dependency list and lockfile hash are
alternatives, not two mandatory inventories. Record effective model/effort
changes on turn/model spans; an initial run header cannot describe all turns.
Keep actual response model and unknown token/cost accounting separate. Export redacts paths/hosts/user names throughout `env` too;
build hashes and versions are retained for reproducible offline comparisons.

## Offline analysis

[Learning conventions](learning.md) define optional comparability identities,
grades and hill-climbing/RL consumption, all in `data`. The
[learning join](../examples/learning.py) demonstrates these conventions without
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

See [ROADMAP.md](../ROADMAP.md) for remaining format/SPEC proposals.

## Check and share

Install from the checkout (`python3 -m pip install .`) for the **flattrace** CLI:

```sh
flattrace check TRACE.jsonl
flattrace export TRACE.jsonl CHILD.jsonl --out reviewed-share
```

`python3 -m flattrace` is the equivalent checkout invocation:

```sh
python3 -m flattrace check TRACE.jsonl
python3 -m flattrace check --events TOOL/flattrace-events.json TRACE.jsonl
# One run, several owners: repeat --events, one file per vocabulary.
python3 -m flattrace check --events RECORDER/flattrace-events.json \
  --events PLUGIN/flattrace-events.json MIXED.jsonl
# Legacy command traces can select coverage without adding a data.command field:
python3 -m flattrace check --events TOOL/flattrace-events.json --command summary OLD.jsonl
python3 -m flattrace export TRACE.jsonl CHILD.jsonl --out shared --path scene.blend
```

The [JSON Schema](../schema/event.schema.json) checks only the envelope; the checker
also pairs spans and validates run boundaries. With `--events`, it checks each
owner's required events and command rules (or a commandless contract's
`required` list) and rejects undeclared names/kinds **per run**, not satisfied
by another run. Every event file must declare `run` as a `span`. The reserved
`run` declaration that composed vocabularies copy is
[../schema/run-declaration.json](../schema/run-declaration.json):

```json
{
  "kind": "span",
  "fields": {}
}
```

Owner-specific measurement prose belongs on the events that own those
measurements, never in that shared block. The cross-tool conventions
(`annotate`, `annotation`, `verdict`, `action`, `changes`) are declared
in [schema/conventions-events.json](../schema/conventions-events.json)
(tool `flattrace-conventions`), which copies the reserved `run`
declaration above: vocabularies use those names only with the
convention's meaning and never redefine them, so composition never sees
conflicting convention definitions. Counters expose removed
interior lines and reordering when sequences become discontinuous; they do not
prove durability, detect every lost suffix or order different processes.
A sink failure disables the environment writer rather than recovering missing
writes. Existing v1 traces remain accepted without seq/pid/new data fields,
including commandless event contracts
or command coverage selected explicitly with `--command`. Tool tests exercise their operations and
branches with this command. Payload field descriptions are documentation, not
an invented schema engine. Writers emit only valid Unicode scalar strings:
lone surrogates are replaced with U+FFFD plus a `data.notes` entry (valid
surrogate pairs stay their character; colliding keys keep the first with a
note). Legacy lines carrying lone-surrogate escapes still parse, and every
checker reports the same warning-level notice
(`line N: lone surrogate escape`) instead of dropping the line.

### Composition truth table

One run file can carry events from several owners. The checker validates the
run against the union of the supplied vocabulary files, deterministically, so
the Python, Rust, CLI and Node checkers agree. The run-start manifest is the
`data.vocabularies` list `[{tool, version, sha256}]` the recorder writes for
the vocabularies actually registered before the writer opened; `sha256` is the
SHA-256 of the exact file bytes parsed, and `version` is descriptive metadata.

| Case | Ruling |
|---|---|
| Run declares `data.vocabularies` | Only the listed vocabularies may declare this run's events. A listed entry whose file is missing or has a different hash is an error; a supplied file that is not listed fails and is named, and never widens the allowed set. Matching pairs with different `tool` names are an error. |
| Hash | SHA-256 of the exact file bytes that were parsed. No canonicalization. A recorder registers a vocabulary by file, not as an in-memory object. |
| `version` | Descriptive only; the vocabulary file itself has no version field. The hash is the check. |
| Byte-identical files | Deduplicated by hash before the owner check. Distinct files claiming the same `tool` are an error naming both. |
| Event equality | Parsed-definition equality of the declaration object: object key order does not count, array order does, field descriptions count. A different definition of the same name is an error naming both files. |
| `run` | Every file declares `run` as a span. Composed files agree on it by copying the reserved declaration above; any differing definitions collide like any other event. |
| One file / no manifest | Today's behaviour, including envelope `tool` == vocabulary `tool`. A command contract still needs a selection before per-row validation runs. |
| Manifest present | Composition mode even with one matched file: the envelope `tool` is the writing program and need not equal any vocabulary `tool`. |
| No manifest, several files | The check composes the files it was given. Lack of fingerprints is unverified provenance, reported as a notice (`unverified provenance: run has no data.vocabularies`), not a failure and not invented provenance. |
| Required and commands | Scoped to the owning vocabulary. A requirement with no command applies to that vocabulary's own events. A file with commands imposes nothing when the run's command is another owner's; a command ID in two files is an error; a command no composed file declares is undeclared. |
| Malformed manifest | A present `data.vocabularies` that is not a list of `{tool, sha256}` entries fails (`invalid data.vocabularies in run start`). |
| Notices | Non-failing warnings reported beside issues: unverified provenance and legacy lone-surrogate escapes. Check exit codes ignore them. |

Local detail includes paths/hosts, **never credentials**. Export is the one
redaction route for report-a-bug and any per-tool opt-in upload. It discovers
absolute Unix/Windows and `./`, `../`, `~/` path tokens, header/`env.host.name` hosts and the
current user. Supply repeated `--path`, `--host`, `--user` for other names,
ambiguous relative paths or paths with spaces. Exact names are replaced in
embedded text and keys too, consistently across all files. Export also masks
credential-looking assignments/flags, Basic/Bearer Authorization and HTTP URL
credentials. It is **not a secret detector** or automatic anonymizer. Review
paths, hosts, users and error text; supply other/URL hostnames explicitly.
Known envelope/header/grade/causal metadata keeps its structural names and
IDs, even when a private hostname happens to equal an attribute name.
Only the reviewed export bundle (numbered JSONL files, plus optional tail notes,
in a fresh directory) may be shared. Originals remain untouched. No uploader,
retention policy, blobs or manifest machinery.

Unfinished runs/spans remain unfinished in exports. An invalid unterminated final
line (including split UTF-8) is omitted with a numbered `.tail.txt` note; its raw
bytes are never shared. Other invalid input refuses export. Check/export exit 0
for complete valid input, 1 for validation issues/unfinished evidence or an omitted
tail, 2 for invocation/I/O failure. Cleanup is the operator's choice. Readers
for check/export load selected files; stats streams them. Validate then use `jq` to filter facts before
feeding a long session into model context—no index or rotation is implied.

