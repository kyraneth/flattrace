# Learning conventions (all in data)

These optional conventions support benchmark, hill-climbing and RL consumers of
[flattrace v1](../SPEC.md). They do not add envelope fields, a payload schema,
checker behavior or replay authority.

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

The [environment vocabulary](environment.md)
describes known-facts `env`; labels alone are not immutable input identities.

## Grades and recording termination

A grader writes `verdict` point data `{trace, run, name, correct, score, reward,
reason, grader}`: target IDs, score name, boolean correctness, numeric score and
reward (each null if unknown), explanation, `{name, version, kind}` grader
identity (`code`, `model`, `human`, or tool extension). Optional target `span`
enables step grading; checker-run envelope IDs are not target IDs. Multiple
grades remain observations, never implicit last-wins. Recording end vs missing
end is terminated vs truncated; error/cancelled ends are not Gymnasium MDP
success claims. Only verdicts assert correctness/reward. Exclude unfinished,
ungraded, cancelled/unknown runs from reward claims; null is not zero.
[The learning example](../examples/learning.py) joins these facts without an optimizer.

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
[offline analysis](../README.md#analysing-traces) for the streaming reader's
measurement limits.
