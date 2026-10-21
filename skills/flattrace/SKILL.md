---
name: flattrace
description: "Instruments tools with flattrace and analyses offline JSONL traces. Use when adding step tracing, writing flattrace-events.json, diagnosing unfinished runs, checking event coverage or finding slow spans with flattrace stats."
---

# Instrument and analyse with flattrace

This is for a **consumer tool**, not development of the flattrace library itself.
For API details or a complete event contract read [the reference](references/instrumentation.md).
In a standalone skill installation, its upstream links lead back to the public spec.

1. Choose static action/decision names, not IDs or filenames. Use spans for
   measured work and points for facts/transitions; put dynamic values and units
   in data. Record useful aggregates, not copied reports or secrets.
2. Write the tool-owned `flattrace-events.json`: declare `run` as a span,
   every emitted name/kind and payload field descriptions with units. Required
   events belong to each command/branch, not a single success-only checklist.
3. Use environment writers so unavailable trace storage never stops product work.
   Always do the work when tracing is off. Preserve product exceptions and their
   actual outcome; never catch-and-ignore an error around the whole instrumented
   operation. Sink I/O is best effort; structural misuse is not.
4. At level 1 emit actions, outcomes, transitions and count/window summaries.
   Gate per-item/poll/redraw detail on `writer.level >= 2`. Writers record the
   level but do **not** filter calls. Declare detail events' level in the contract.
5. One writer/file per process; one owning thread/event loop. Parent child
   processes to the enclosing span using traceparent, inheriting directory and
   level. End each local span once, with its actual outcome, then close/finish
   the run. **Never call end(root)**; never invent missing crash/kill ends.
6. Exercise success, refusal, errors, disabled tracing and unavailable sinks.
   Run `flattrace check --events flattrace-events.json TRACE...` on each branch;
   missing/undeclared events or unfinished work are findings, not permission
   to fabricate records. The checker validates shape and coverage, not payload meaning.
7. Run `flattrace stats TRACE_OR_DIRECTORY...` (or `--json`). Start with run
   status/issues, then slow runs and dominant span names. Unknown is not zero;
   nested inclusive totals are not elapsed time and self time may be unknown.
8. Before sharing, use `flattrace export TRACE... --out reviewed-share`,
   supply ambiguous private names explicitly and inspect the bundle. Raw local
   paths/hosts and sensitive error prose are not safe just because checking passes.

## Common mistakes

- A span per item at level 1: aggregate counts/results there; keep raw detail
  at level 2+. Do not remove meaningful command outcomes to reduce noise.
- Ending the root directly or closing with active spans: close/finish owns
  the root; async children may outlive nonroot parents but must end before the run.
- A broad catch hides errors and silently kills the trace: keep error handling
  around the real operation, record the actual failure when possible, and
  re-raise/return the original product failure. Unfinished evidence is preferable
  to a false successful run.
