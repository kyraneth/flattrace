# flattrace reader contract (frozen for the 0.2.0 engine)

Status: frozen. This document is the compatibility contract for the reader
engine. The conformance corpus lives in `tests/conformance/corpus/` with
golden outputs in `tests/conformance/golden/`; `tests/conformance/reference.py`
is the test-only pure-Python implementation that generated them, and
`tests/test_conformance.py` asserts them. The Rust engine
(`crates/flattrace-read`), the `flattrace-read` CLI, the Python bindings and the
Node bindings MUST reproduce every golden file exactly (parsed-JSON equality;
floats compare with 1e-9 relative tolerance). Changing any behavior below
requires new goldens, a contract revision here, and all four surfaces updated
together.

## Inputs

Functions take explicit file paths: plain `.jsonl` and gzip `.jsonl.gz` with
identical JSONL content after decompression. There is no directory walk and no
network or index machinery. Files are read in the order given. Runs are
deduplicated by `(trace, run)`; the first path wins and later copies are listed
as duplicates: still fully validated, but their duration/error samples are
excluded. Files with no valid rows are reported as invalid and contribute no
run. Single-run-per-file `check` semantics are exactly `flattrace.check`;
corpus analysis never merges divergent copies of one run.

## Functions

- `check(paths, events?, command?)` — per-file validation. Each entry holds
  `file` (echoed as given), `rows` (valid row count) and `issues` (the exact
  `read_trace` strings, plus `check_events` strings when a contract is given).
- `spans(paths, filter?)` — paired rows, one per span start in file order.
  Unfinished spans stay unfinished: `end_t`, `end_seq`, `took_ms`, `outcome`
  and `end_data` are null, `unfinished` is true. Each row carries exact source
  pointers (`file`, `start_seq`, `span`), wall start/end timestamps `start_t`
  and `end_t`, `depth` (open local spans at start; the local run root is 0),
  `children` (direct local child span IDs), `data` (start payload), `end_data`,
  `error` (end error object or null) and attached `points` (in `seq` order).
- `digest(paths, scope?)` — compact per-`(tool, event)` table plus an error
  census, an unfinished-span pointer list and a coverage statement. The table
  holds `n` (spans, finished and unfinished), `took` and `self` summaries,
  `self_unknown` reason counts, `errors` (`records` vs `occurrences`),
  `unfinished` and the `slowest` span pointer (max `took_ms`; null when all
  unknown). The census groups by `(tool, event, kind, where)` with record and
  occurrence counts, a message sample and one example pointer. `where` comes
  from `data.where`; an outcome-error end without an error object is a record
  with null kind and message.
- `group(paths, by, metrics?, filter?)` — group paired rows by key specs and
  compute metrics per group. Output is a list of `{key, ...metrics}` entries;
  group keys with missing facts are null.
- `tree(paths, trace)` — process tree for one trace ID across the corpus:
  roots (no in-corpus parent run), children slowest-first, per-node run
  pointers, span/point counts, `failed` (outcome `error`) and slowest run
  summaries. Runs whose parent span is in no corpus run are roots with
  `parent_outside_corpus: true`.
- `compare(a_paths, b_paths, filter?)` — per-`(tool, event)` side summaries
  with `delta_n`, `delta_p50` and `delta_total` (`b - a`; null when either side
  is unknown), plus per-side coverage and per-run outcome/duration lists.
- `overlap(paths, window_event, span_filter?)` — wall-timestamp intersection
  of window spans (event `window_event`) with spans matching `span_filter`.
  Windows without an end are reported under `unbounded_windows` with no
  overlap claim. Hits carry span pointers with their `took_ms` and outcome;
  the reader never derives a duration from wall timestamps (see Policies).

`digest`, `group`, `tree`, `compare` and `overlap` outputs all embed
`coverage`: `{files, runs, complete_runs, unfinished_runs, invalid_files,
duplicates_excluded, spans, spans_unfinished, points, issues, statement}`.
`statement` is the same counts as one human sentence. The `issues` count
covers run/pairing/boundary evidence per run; malformed-line details are
check-level evidence and appear in `check` output, not here.

## Filters

`filter` is a flat object; every present field must match:

| Field | Meaning |
|---|---|
| `tool`, `event`, `trace`, `run` | envelope equality; `event` accepts one name or a list |
| `outcome` | end-outcome equality; the value `"unfinished"` matches unfinished spans (whose outcome is null, never `"unfinished"` as a string) |
| `unfinished` | boolean |
| `agent` | run-start `data.env["agent.name"]` |
| `model` | span-start `data["gen_ai.request.model"]`, else `data["model"]`, else run-start env `gen_ai.request.model` |
| `session` | span-start `data.session_id`/`data.session`, else run-start `session_id`/`session` |
| `error_kind` | any attached error (end or point) with this `kind` |
| `t_from`, `t_to` | inclusive RFC3339 bounds on `start_t` (compared as timestamps, so `Z` and `+00:00` mix freely) |
| `data` | object of dotted-path → exact value matched against the span-start payload |

Group key specs (`by`) are `tool`, `event`, `outcome` (unfinished spans bucket
as `"unfinished"`), `agent`, `model`, `session`, `run`, `trace`, `data.<path>`
against the span-start payload, or `error.kind` / `error.where` against
attached errors (multiple distinct values sort and join as a list). Unknown key
names are an error.

Metrics (`metrics`, default all): `n` (rows), `took` (nearest-rank over known
`took_ms`), `self` (nearest-rank over known `self_ms`), `errors`
(`records`/`occurrences`), `unfinished` (count), `points` (attached-point
count). `took`/`self` are null when no value is known, never zero-filled.

## Summaries and pairing policy

Summaries are exact nearest-rank `{n, p50, p95, max, total}` over known values
only: ascending sort, rank `ceil(n·p)`, 1-indexed. `total` is the plain sum
(integer when every sample is an integer). Selection preserves the observed
JSON number; no interpolation, no histogram, no sampling. `self_ms` is the
conservative local unattributed residence from `flattrace.stats`: parent
`took_ms` minus sequential fully-contained direct local children, in
milliseconds as a float. `self_unknown` names the reason instead of guessing:
`child outlives parent`, `async descendant`, `unknown duration`, `children
exceed parent duration`, `unknown child duration`, `unfinished`, or
`incomplete or invalid run` (any run carrying issues keeps no known self time).
Cross-process children never subtract. Depths, child sets and ancestor marking
follow the same pairing pass.

## Policies (non-negotiable)

- Unknown never becomes 0: null durations, scores and facts stay null and are
  excluded from summaries rather than zero-filled. Occurrence counts
  (`data.count`) expand error/warning records only, never span samples.
- Unfinished stays unfinished: missing ends are evidence, never inferred
  success; unbounded windows make no overlap claim.
- Durations come only from `took_ms`. Wall timestamps correlate (filtering,
  window intersection, ordering); they never produce a duration.
- Errors count explicit evidence: an `error` object, `outcome: "error"`, or
  `data.severity: "error"`. Warnings count only explicit
  `data.severity: "warn"`/`"warning"`.
- Reports join local facts only. Pi session/entry IDs travel in payload data
  (for example `session_id`); the engine parses no Pi journal.

## CLI mapping (Rust `flattrace-read`, step 2)

`check [--events FILE] [--command ID]`, `stats` (the `flattrace.stats` report,
unchanged shape), `digest [--filter JSON]`, `spans [--filter JSON]`,
`tree --trace ID`, `group --by a,b [--metrics m,n] [--filter JSON]`,
`compare --a A.jsonl [--a ...] --b B.jsonl [--b ...]`,
`overlap --window EVENT [--filter JSON]`, each with `--json` (default) and a
compact text form. `spans` is a row query and exits 0 on success; the other
commands exit 1 when the corpus carries validation or unfinished evidence
and 2 on invocation or I/O failure. Filters arrive as one JSON object with
the fields above.

## Parser-detail note (frozen)

Issue identity is the category prefix; malformed-JSON detail text is
informative. For issues whose detail comes from the JSON parser itself
(`line N: ...` / `incomplete tail N: ...` where the remainder is not one of
the frozen validation messages below), ports compare only the prefix. Every
other issue string is byte-identical across implementations. The frozen
validation messages are: `missing envelope fields`,
`seq must be a nonnegative integer`, `v must be 1`, `<field> must be a string`
for `tool`, `tool_version`, `run`, `event`, `state` and `t`, `invalid trace`,
`invalid span`, `invalid parent`, `run is reserved for lifetime spans`,
`invalid state or data`, `t must be RFC3339 UTC`,
`end requires took_ms and outcome`, `invalid took_ms`,
`took_ms and outcome belong on ends`,
`error requires string kind and message`, plus the run-boundary strings
`missing run start and end`, `first line must be run start`,
`run start requires argv, host, version`, `run <task|variant> must be a string`,
`file contains different runs or tools`, `unfinished run <id>: missing run end`,
`last line must be the matching run end`, `expected one run start`,
`seq gap or reordering: expected <n>, got <m>`, `duplicate span start <span>`,
`inactive parent for <span>`, `unmatched end <span>`,
`point outside active span <span>`, `unfinished span <span>: missing end`,
`incomplete gzip archive: ...`, and the contract strings
`event list tool mismatch`,
`select a command with --command (or run data.command) for this contract`,
`undeclared command '<id>'`, `undeclared event <name>`,
`wrong event kind <name>`, `missing required event <name> for <command>`,
`level <n> event <name> in level <m> run`.

## Versioning

Additive output fields are allowed; renaming, removing or redefining a field,
or changing pairing, summary or coverage semantics, requires a contract
revision, regenerated goldens and a minor version bump of every binding.
