# API and event-contract reference

The [upstream spec](https://github.com/kyraneth/flattrace/blob/main/SPEC.md)
is normative; [writer/analysis reference](https://github.com/kyraneth/flattrace/blob/main/docs/reference.md)
and [quickstarts](https://github.com/kyraneth/flattrace#quickstart) cover API details.
The skill does not define another recording format.

## Event file

A command-aware example (field descriptions are documentation, not a schema):

```json
{
  "tool": "demo",
  "events": {
    "run": {"kind": "span", "fields": {"command": "command/branch ID"}},
    "calculate": {"kind": "span", "fields": {"items": "input item count"}},
    "result": {"kind": "point", "fields": {"value": "computed total"}},
    "item": {"kind": "point", "level": 2, "fields": {"index": "item index"}}
  },
  "commands": {
    "calculate.ok": ["run", "calculate", "result"],
    "calculate.error": ["run", "calculate"]
  }
}
```

Set run-start `data.command` to the exercised branch. For older/commandless
traces, select it with `check --events flattrace-events.json --command calculate.ok TRACE`.
A commandless contract may instead use top-level `required`. Requirements
are checked per run, not satisfied by a different file; higher-level events are
not required in lower-level runs.

## Activation and lifecycle

| Language | Environment constructor | Child context | End run |
| --- | --- | --- | --- |
| Python | `Writer.from_env(tool, version, argv=..., data=...)` | `w.child_env(span)` | `w.close(outcome)` |
| Rust | `Writer::from_env(tool, version, argv, host, data)?` | inherit env; set `FLATTRACE_TRACEPARENT=w.traceparent(Some(span))?` | `w.finish(outcome)?` |
| Node | `Writer.fromEnv(tool, version, {argv, data})` | `w.childEnv(span)` | `w.close(outcome)` |

Unset/empty `FLATTRACE_DIR` or level 0 means no trace I/O. Environment APIs
return no writer when off/unavailable; the operation must still run.
Level defaults to 1; invalid levels fall back to 1 with a note. Structural
mistakes such as malformed traceparent still surface as errors.
Explicit-path constructors are strict, suited to tests and deliberate output.

Use `start` to allocate a span and `end` with its actual outcome (`ok`,
`error`, `refused`, `cancelled`, `unknown`, or a documented extension).
An error object must have string `kind` and `message`; never put credentials in it.
Points reuse the enclosing span ID **and its parent**, not a new child ID.
After fork create a fresh writer. Dropping/exiting never invents ends.

## Interpreting analysis

`check` validates the stream and lifecycle. `stats` accepts files/directories
and gzip inputs, deduplicates by (trace, run), and reports issues as well as
durations. Duplicate copies are still validated; first-copy wins, never
merge endings. Inclusive durations include waits and instrumentation, not
just CPU. Self time excludes only safe contained, sequential local children;
overlap, cross-process attribution and incomplete evidence limit interpretation.
Errors count explicit error evidence; warnings require explicit payload severity,
not guessed prose. A clean report does not prove absence of unrecorded events.
