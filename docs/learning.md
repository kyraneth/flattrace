# Learning conventions (all in data)

These optional conventions support benchmark, hill-climbing and RL consumers of
[flattrace v1](../SPEC.md). They do not add envelope fields, a payload schema,
checker behavior or replay authority.

## Glossary

- **format**: the flattrace v1 envelope. Not a catalogue of every tool's events.
- **writer**: serializes and flushes one run file. Does not decide what a host
  should observe.
- **recorder**: host hooks that emit a vocabulary (for example, a generic
  recorder for an agent harness).
- **event vocabulary**: one owner's `flattrace-events.json`. Several may apply
  to one run; `flattrace check` validates the run against their union.
- **convention**: an optional cross-tool `data` shape. Not an envelope change.
- **reader / checker**: pairs spans, checks structure and declared vocabulary.
  Never asserts payload meaning.
- **detector**: notices a shape and writes an annotation. The package that owns
  the shape owns the detector.
- **annotation**: a detector's suggestion. Never correctness, never authority,
  no last-wins.
- **grader**: compares an output to a reference and writes a verdict.
- **verdict**: a grader's assessment. Several grades remain observations.
- **action**: an observed operation (read, edit, write, exec, net, send).
- **changes**: an observed file delta at settle. Not exclusive authorship.

Annotate is the detector's work; an annotation is its suggestion; a verdict is
a grader's assessment; an action is an observed operation; changes are an
observed delta.

## Identities, comparability and causality

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

The [environment vocabulary](reference.md#environment-and-build-provenance)
describes known-facts `env`; labels alone are not immutable input identities.

## Grades and recording termination

A grader writes `verdict` point data `{trace, run, name, correct, score, reward,
reason, grader}`: target IDs, score name, boolean correctness, numeric score and
reward (each null if unknown), explanation, `{name, version, kind}` grader
identity (`code`, `model`, `human`, or tool extension). Optional target `span`
enables step grading; checker-run envelope IDs are not target IDs. Multiple
grades remain observations, never implicit last-wins. A reference URI and a
coverage field would be useful, but they are a proposal, not this contract:
consumers are not migrated and a verdict is not rejected for lacking them.
Recording end vs missing end is terminated vs truncated; error/cancelled ends
are not Gymnasium MDP success claims. Only verdicts assert correctness/reward.
Exclude unfinished, ungraded, cancelled/unknown runs from reward claims; null
is not zero.
[The learning example](../examples/learning.py) joins these facts without an optimizer.

## Detectors, annotations, actions and changes

A detector notices a shape; an annotation is its suggestion. Annotations never
assert correctness or reward, are never promoted to verdicts, and never resolve
by last-wins: a re-label names the earlier annotation in `supersedes`,
explicitly. Re-labels from a later pass live in separate detector-run files
against the same target IDs and `input_sha256`.

An `annotate` span is the detector call itself: `data` carries
`{detector: {name, version, kind}, questions, input_sha256, usage, cost}`.
`detector.kind` is `model`, `code` or `human`. `questions` names what was
asked; `input_sha256` fingerprints the detector input; `usage` and `cost` are
null when unknown. The span ends `ok`, `error`, `refused`, or `cancelled`
when the recorder closes first. Model labels use an `annotate` span because
the call has a lifecycle and a cost.

An `annotation` point carries `{target, id, detector, label, probability,
input_sha256}` with optional `supersedes`. `target` names the annotated
occurrence: `{trace, run, span?, entryId?, t_from?, t_to?, seq?}`, where `seq`
with file and run names one point occurrence (points share their span's ID,
so they have no identity of their own). `id` is stable across re-labels;
`supersedes` names the replaced annotation ID. `detector` repeats the calling
detector identity. `probability` is null when the detector does not return
one (deterministic rules included). Code detectors running in-process may
point on the run span. A detector that finds secret-shaped values records
only the location and the rule ID, never the value.

An `action` point records a known operation: `{kind, paths, bytes_changed,
lines_added, lines_removed, argv, exit_code}`, every field null when unknown.
`kind` is `read`, `edit`, `write`, `exec`, `net` or `send` for a named,
versioned allowlist of built-in tools, and null otherwise: a custom tool gets
`kind: null`, never a guess. `argv` passes the writer's `sanitizeArgv` plus
tool-specific redaction before storage; a planted secret on an `action` is
masked, and the value is absent from the annotation. `exit_code` is the
observed process result, null when unknown.

A `changes` point at settle records the observed file delta: `{files: [{path,
added, removed}], source, coverage}`. `source` is `git` (porcelain status
taken in the working directory) or `fs`; `coverage` states what was not seen
(untracked or ignored paths are blindness, not absence). Changes are an
observed delta, not authorship: they do not claim the traced process made
every edit.

Model spans may carry a `gen_ai` usage convention:
`gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens`, the response
model and finish reasons, each null when unknown, so cross-tool cost
comparison stays possible without inventing zeros.

`action` and `changes` are level-2 detail: recorders may emit them only when
the run asks for per-item verbosity, like any other level-2 event. The
checker validates the event names and kinds like any declared event; it does
not validate convention payloads.

The machine-checkable declarations for these shapes live in
[schema/conventions-events.json](../schema/conventions-events.json)
(tool `flattrace-conventions`), which copies the reserved `run`
declaration from `schema/run-declaration.json`. A vocabulary uses the
convention names (`annotate`, `annotation`, `verdict`, `action`,
`changes`) only with the convention's meaning and never redefines them,
so composed runs never see conflicting convention definitions.

## Hill-climbing / RL consumption

`learning.py` deduplicates input runs on `(trace, run)` and emits one observation
per verdict with task/variant, episode/attempt, seed and task/config hashes,
correctness, named score, reward, structured grader, run outcome and measured
duration. Run recording termination is not an MDP success claim. A hill-climber
groups identical task digests, score names and grader names/versions, admits
correct candidates, then compares comparable variants' run cost/duration; it
does not treat a failed or missing grade as faster success.
An RL pipeline uses explicit rewards (not an assumed score alias) alongside the same run IDs,
linking actions back to local Pi journal pointers when available. Null scores
or rewards are unscored/unrewarded, never zero-filled. Repeated grades are
separate observations; the consumer chooses its adjudication policy. Raw
failed/unfinished runs remain available for bug hunting even when omitted from
reward data. Benchmark tooling owns workload/input identities and comparable
environments in run data; this format does not certify benchmark validity or
implement an optimizer.

## Synthetic example identities

The [benchmark fixture](../tests/fixtures/synthetic/3.jsonl)'s `task_hash` hashes
the bytes `synthetic-scene-fps-task`; `config_hash` hashes
`synthetic-candidate-config`, both SHA-256. Seed/episode/attempt are explicitly
synthetic. Actual benchmark tools document their own immutable hash inputs.
See [examples](../examples/README.md) for commands and
[offline analysis](../README.md#check-and-analyse) for the streaming reader's
measurement limits.
