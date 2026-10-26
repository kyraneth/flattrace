# SPDX-License-Identifier: MIT
"""Build the deterministic conformance corpus (synthetic only, no private data).

Run: python3 tests/conformance/make_corpus.py
Output: tests/conformance/corpus/*.jsonl (+ one .gz) and agent-events.json.
Every ID, timestamp and duration is fixed in this script; reruns are byte-identical.
"""
import gzip
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "corpus"

T1 = "a" * 32  # shared traceparent tree: c01 parent + c02 child
TB, TC, TD, TE, TF, TG, TH, TI = ("b" * 32, "c" * 32, "d" * 32, "e" * 32,
                                  "ab" * 16, "cd" * 16, "12" * 16, "ef" * 16)


def row(seq, tool, version, run, trace, span, parent, event, state, t,
        data=None, took=None, outcome=None, error=None):
    value = {"v": 1, "seq": seq, "tool": tool, "tool_version": version,
             "run": run, "trace": trace, "span": span, "parent": parent,
             "event": event, "state": state, "t": t, "data": data or {}}
    if state == "end":
        value["took_ms"] = took
        value["outcome"] = outcome
    if error is not None:
        value["error"] = error
    return value


def run_start(seq, tool, version, run, trace, span, parent, t, data):
    header = {"argv": ["synthetic"], "host": "synthetic", "version": version,
              "pid": 4242, **data}
    return row(seq, tool, version, run, trace, span, parent,
               "run", "start", t, header)


def write(name, rows):
    path = CORPUS / name
    path.write_text("".join(json.dumps(r) + "\n" for r in rows) + "" if rows else "")
    return path


def main():
    CORPUS.mkdir(exist_ok=True)
    for path in CORPUS.iterdir():
        if path.name != "real":
            path.unlink() if path.is_file() else None

    # c01: agent parent run, clean, with agent/model/session facts.
    c01 = [
        run_start(0, "agent", "synthetic", "c01-parent", T1,
                  "1111111111111111", None, "2026-10-20T00:00:00.000Z",
                  {"env": {"agent.name": "synthetic-agent",
                            "gen_ai.request.model": "synthetic-model-a"},
                   "session_id": "session-1", "task": "synthetic-task",
                   "variant": "baseline"}),
        row(1, "agent", "synthetic", "c01-parent", T1,
            "2222222222222222", "1111111111111111",
            "agent.turn", "start", "2026-10-20T00:00:00.001Z",
            {"model": "synthetic-model-b", "effort": "high"}),
        row(2, "agent", "synthetic", "c01-parent", T1,
            "3333333333333333", "2222222222222222",
            "tool.call", "start", "2026-10-20T00:00:00.002Z",
            {"tool": "oxblend", "args_hash": "abc123", "session_id": "session-1"}),
        row(3, "agent", "synthetic", "c01-parent", T1,
            "3333333333333333", "2222222222222222",
            "tool.call", "end", "2026-10-20T00:00:00.032Z", {},
            took=30, outcome="ok"),
        row(4, "agent", "synthetic", "c01-parent", T1,
            "2222222222222222", "1111111111111111",
            "agent.turn", "end", "2026-10-20T00:00:00.033Z", {},
            took=32, outcome="ok"),
        row(5, "agent", "synthetic", "c01-parent", T1,
            "1111111111111111", None,
            "run", "end", "2026-10-20T00:00:00.034Z", {},
            took=34, outcome="ok"),
    ]
    write("c01-parent.jsonl", c01)

    # c02: child run sharing trace T1, parented to c01's tool.call span.
    c02 = [
        run_start(0, "agent", "synthetic", "c02-child", T1,
                  "4444444444444444", "3333333333333333",
                  "2026-10-20T00:00:00.040Z", {}),
        row(1, "agent", "synthetic", "c02-child", T1,
            "4444444444444444", "3333333333333333",
            "input", "point", "2026-10-20T00:00:00.041Z",
            {"path": "shots/sh002/scene.blend"}),
        row(2, "agent", "synthetic", "c02-child", T1,
            "4444444444444444", "3333333333333333",
            "run", "end", "2026-10-20T00:00:00.045Z", {},
            took=5, outcome="ok"),
    ]
    write("c02-child.jsonl", c02)

    # c03: async child outliving its launcher + overlapping children.
    c03 = [
        run_start(0, "agent", "synthetic", "c03-async", TB,
                  "aaaaaaaaaaaaaaaa", None, "2026-10-20T00:00:01.000Z", {}),
        row(1, "agent", "synthetic", "c03-async", TB,
            "bbbbbbbbbbbbbbbb", "aaaaaaaaaaaaaaaa",
            "launch", "start", "2026-10-20T00:00:01.001Z", {}),
        row(2, "agent", "synthetic", "c03-async", TB,
            "cccccccccccccccc", "bbbbbbbbbbbbbbbb",
            "worker", "start", "2026-10-20T00:00:01.002Z", {}),
        row(3, "agent", "synthetic", "c03-async", TB,
            "bbbbbbbbbbbbbbbb", "aaaaaaaaaaaaaaaa",
            "launch", "end", "2026-10-20T00:00:01.011Z", {},
            took=10, outcome="ok"),
        row(4, "agent", "synthetic", "c03-async", TB,
            "cccccccccccccccc", "bbbbbbbbbbbbbbbb",
            "worker", "end", "2026-10-20T00:00:01.027Z", {},
            took=25, outcome="ok"),
        row(5, "agent", "synthetic", "c03-async", TB,
            "dddddddddddddddd", "aaaaaaaaaaaaaaaa",
            "overlap-a", "start", "2026-10-20T00:00:01.030Z", {}),
        row(6, "agent", "synthetic", "c03-async", TB,
            "eeeeeeeeeeeeeeee", "aaaaaaaaaaaaaaaa",
            "overlap-b", "start", "2026-10-20T00:00:01.031Z", {}),
        row(7, "agent", "synthetic", "c03-async", TB,
            "dddddddddddddddd", "aaaaaaaaaaaaaaaa",
            "overlap-a", "end", "2026-10-20T00:00:01.040Z", {},
            took=10, outcome="ok"),
        row(8, "agent", "synthetic", "c03-async", TB,
            "eeeeeeeeeeeeeeee", "aaaaaaaaaaaaaaaa",
            "overlap-b", "end", "2026-10-20T00:00:01.041Z", {},
            took=10, outcome="ok"),
        row(9, "agent", "synthetic", "c03-async", TB,
            "aaaaaaaaaaaaaaaa", None,
            "run", "end", "2026-10-20T00:00:01.042Z", {},
            took=40, outcome="ok"),
    ]
    write("c03-async.jsonl", c03)

    # c04: unfinished run with inactive parent, unmatched end, stray point.
    c04 = [
        run_start(0, "agent", "synthetic", "c04-unfinished", TC,
                  "1111111111111111", None, "2026-10-20T00:00:02.000Z", {}),
        row(1, "agent", "synthetic", "c04-unfinished", TC,
            "2222222222222222", "1111111111111111",
            "work", "start", "2026-10-20T00:00:02.001Z", {}),
        row(2, "agent", "synthetic", "c04-unfinished", TC,
            "3333333333333333", "deadbeefdeadbeef",
            "orphan", "start", "2026-10-20T00:00:02.002Z", {}),
        row(3, "agent", "synthetic", "c04-unfinished", TC,
            "9999999999999999", "1111111111111111",
            "ghost", "end", "2026-10-20T00:00:02.003Z", {},
            took=1, outcome="ok"),
        row(4, "agent", "synthetic", "c04-unfinished", TC,
            "9999999999999999", "1111111111111111",
            "stray", "point", "2026-10-20T00:00:02.004Z", {}),
    ]
    write("c04-unfinished.jsonl", c04)

    # c05: duplicated run, byte-identical under two names.
    dup = [
        run_start(0, "tool", "1", "c05-dup", TD,
                  "1111111111111111", None, "2026-10-20T00:00:03.000Z", {}),
        row(1, "tool", "1", "c05-dup", TD,
            "2222222222222222", "1111111111111111",
            "work", "start", "2026-10-20T00:00:03.001Z", {}),
        row(2, "tool", "1", "c05-dup", TD,
            "2222222222222222", "1111111111111111",
            "work", "end", "2026-10-20T00:00:03.008Z", {},
            took=7, outcome="ok"),
        row(3, "tool", "1", "c05-dup", TD,
            "1111111111111111", None,
            "run", "end", "2026-10-20T00:00:03.009Z", {},
            took=9, outcome="ok"),
    ]
    write("c05-dup-a.jsonl", dup)
    write("c05-dup-b.jsonl", dup)

    # c06: torn tail (truncated final line, no trailing newline).
    c06 = [
        run_start(0, "tool", "1", "c06-torn", "e0" * 16,
                  "1111111111111111", None, "2026-10-20T00:00:04.000Z", {}),
        row(1, "tool", "1", "c06-torn", "e0" * 16,
            "2222222222222222", "1111111111111111",
            "work", "start", "2026-10-20T00:00:04.001Z", {}),
    ]
    path = CORPUS / "c06-torn.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in c06) + '{"v": 1, "tool": "tool"')

    # c07: payload subjects, errors with count, stall window over tool.exec.
    c07 = [
        run_start(0, "synthetic-tool", "1", "c07-payload", TE,
                  "1111111111111111", None, "2026-10-20T00:00:01.000Z",
                  {"session_id": "session-7"}),
        row(1, "synthetic-tool", "1", "c07-payload", TE,
            "2222222222222222", "1111111111111111",
            "tool.exec", "start", "2026-10-20T00:00:01.000Z",
            {"shot": "sh001", "file_path": "shots/sh001/scene.blend"}),
        row(2, "synthetic-tool", "1", "c07-payload", TE,
            "3333333333333333", "1111111111111111",
            "tool.exec", "start", "2026-10-20T00:00:01.050Z",
            {"shot": "sh001", "file_path": "shots/sh001/scene.blend"}),
        row(3, "synthetic-tool", "1", "c07-payload", TE,
            "4444444444444444", "1111111111111111",
            "loop.stall", "start", "2026-10-20T00:00:01.000Z",
            {"window": "event-loop"}),
        row(4, "synthetic-tool", "1", "c07-payload", TE,
            "2222222222222222", "1111111111111111",
            "tool.exec", "end", "2026-10-20T00:00:01.120Z", {},
            took=120, outcome="ok"),
        row(5, "synthetic-tool", "1", "c07-payload", TE,
            "1111111111111111", None,
            "failure", "point", "2026-10-20T00:00:01.100Z",
            {"count": 5, "where": "tool/load.py:44"},
            error={"kind": "io", "message": "coalesced synthetic failure"}),
        row(6, "synthetic-tool", "1", "c07-payload", TE,
            "3333333333333333", "1111111111111111",
            "tool.exec", "end", "2026-10-20T00:00:01.230Z",
            {"where": "tool/fake.py:10"},
            took=180, outcome="error",
            error={"kind": "render", "message": "fake failure at make.py"}),
        row(7, "synthetic-tool", "1", "c07-payload", TE,
            "4444444444444444", "1111111111111111",
            "loop.stall", "end", "2026-10-20T00:00:01.250Z", {},
            took=250, outcome="ok"),
        row(8, "synthetic-tool", "1", "c07-payload", TE,
            "1111111111111111", None,
            "run", "end", "2026-10-20T00:00:01.400Z", {},
            took=400, outcome="ok"),
    ]
    write("c07-payload.jsonl", c07)

    # c08/c09: comparable baseline vs candidate runs with verdicts.
    def compared(seq_trace, run, variant, config, render_took, encode_took,
                 encode_outcome, encode_error, correct, score, run_took, run_outcome):
        rows = [
            run_start(0, "bench", "1", run, seq_trace,
                      "1111111111111111", None, "2026-10-20T00:00:05.000Z",
                      {"task": "synthetic-fps", "variant": variant,
                       "task_hash": "sha256:" + "ab" * 32,
                       "config_hash": "sha256:" + config * 32, "seed": 7}),
            row(1, "bench", "1", run, seq_trace,
                "2222222222222222", "1111111111111111",
                "render", "start", "2026-10-20T00:00:05.001Z", {}),
            row(2, "bench", "1", run, seq_trace,
                "2222222222222222", "1111111111111111",
                "render", "end", "2026-10-20T00:00:05.101Z", {},
                took=render_took, outcome="ok"),
            row(3, "bench", "1", run, seq_trace,
                "3333333333333333", "1111111111111111",
                "encode", "start", "2026-10-20T00:00:05.102Z", {}),
            row(4, "bench", "1", run, seq_trace,
                "3333333333333333", "1111111111111111",
                "encode", "end", "2026-10-20T00:00:05.152Z", {},
                took=encode_took, outcome=encode_outcome, error=encode_error),
            row(5, "bench", "1", run, seq_trace,
                "1111111111111111", None,
                "verdict", "point", "2026-10-20T00:00:05.160Z",
                {"trace": seq_trace, "run": run, "name": "fps",
                 "correct": correct, "score": score, "reward": score,
                 "grader": {"name": "synthetic", "version": "1", "kind": "code"}}),
            row(6, "bench", "1", run, seq_trace,
                "1111111111111111", None,
                "run", "end", "2026-10-20T00:00:05.161Z", {},
                took=run_took, outcome=run_outcome),
        ]
        return rows

    write("c08-baseline.jsonl", compared(
        TF, "c08-baseline", "baseline", "cd", 100, 50, "ok", None, True, 1, 160, "ok"))
    write("c09-candidate.jsonl", compared(
        TG, "c09-candidate", "candidate", "ef", 80, 60, "error",
        {"kind": "encode", "message": "synthetic encode failure"}, False, 0, 150, "error"))

    # c10: gzip member with its own run.
    c10 = [
        run_start(0, "tool", "1", "c10-archive", TH,
                  "1111111111111111", None, "2026-10-20T00:00:06.000Z", {}),
        row(1, "tool", "1", "c10-archive", TH,
            "1111111111111111", None,
            "note", "point", "2026-10-20T00:00:06.001Z", {"n": 1}),
        row(2, "tool", "1", "c10-archive", TH,
            "1111111111111111", None,
            "run", "end", "2026-10-20T00:00:06.002Z", {},
            took=2, outcome="ok"),
    ]
    with gzip.open(CORPUS / "c10-archive.jsonl.gz", "wb") as stream:
        stream.write("".join(json.dumps(r) + "\n" for r in c10).encode())

    # c11: interior invalid lines (bad JSON, bad envelope) then valid rows.
    c11 = [
        run_start(0, "tool", "1", "c11-bad", TI,
                  "1111111111111111", None, "2026-10-20T00:00:07.000Z", {}),
        row(3, "tool", "1", "c11-bad", TI,
            "2222222222222222", "1111111111111111",
            "work", "start", "2026-10-20T00:00:07.001Z", {}),
        row(4, "tool", "1", "c11-bad", TI,
            "2222222222222222", "1111111111111111",
            "work", "end", "2026-10-20T00:00:07.010Z", {},
            took=9, outcome="ok"),
    ]
    bad_envelope = dict(c11[0])
    bad_envelope["v"] = 2
    lines = [json.dumps(c11[0]) + "\n",
             '{"broken": true}\n',
             json.dumps(bad_envelope) + "\n",
             json.dumps(c11[1]) + "\n",
             json.dumps(c11[2]) + "\n"]
    (CORPUS / "c11-bad.jsonl").write_text("".join(lines))

    (HERE / "agent-events.json").write_text(json.dumps(
        {"tool": "agent",
         "events": {name: {"kind": "span", "fields": {}}
                    for name in ("run", "agent.turn", "tool.call")}}, indent=2) + "\n")
    print(f"wrote {len(list(CORPUS.glob('*.jsonl*')))} corpus files")


if __name__ == "__main__":
    main()
