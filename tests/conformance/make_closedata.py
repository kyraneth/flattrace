# SPDX-License-Identifier: MIT
"""Build the run-end-data fixture (deterministic, synthetic, no private data).

Run: python3 tests/conformance/make_closedata.py
Output: tests/conformance/corpus/closedata/01.jsonl
A run whose root end carries a data payload (run totals), exercising the
close-data writer API (close(data=...) / finish_with) on the reader side.
Every ID, timestamp and duration is fixed in this script; reruns are
byte-identical.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "corpus" / "closedata"


def row(seq, tool, version, run, trace, span, parent, event, state, t,
        data=None, took=None, outcome=None):
    value = {"v": 1, "seq": seq, "tool": tool, "tool_version": version,
             "run": run, "trace": trace, "span": span, "parent": parent,
             "event": event, "state": state, "t": t, "data": data or {}}
    if state == "end":
        value["took_ms"] = took
        value["outcome"] = outcome
    return value


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    trace = "ab" * 16
    rows = [
        row(0, "tool", "1", "closedata", trace,
            "1111111111111111", None, "run", "start",
            "2026-10-20T00:00:08.000Z",
            {"argv": ["synthetic"], "host": "synthetic", "version": "1",
             "task": "synthetic-task", "variant": "baseline"}),
        row(1, "tool", "1", "closedata", trace,
            "2222222222222222", "1111111111111111",
            "agent.turn", "start", "2026-10-20T00:00:08.001Z",
            {"model": "synthetic-model"}),
        row(2, "tool", "1", "closedata", trace,
            "3333333333333333", "2222222222222222",
            "tool.exec", "start", "2026-10-20T00:00:08.002Z",
            {"shot": "sh009"}),
        row(3, "tool", "1", "closedata", trace,
            "3333333333333333", "2222222222222222",
            "tool.exec", "end", "2026-10-20T00:00:08.014Z", {},
            took=12, outcome="ok"),
        row(4, "tool", "1", "closedata", trace,
            "2222222222222222", "1111111111111111",
            "agent.turn", "end", "2026-10-20T00:00:08.016Z", {},
            took=15, outcome="ok"),
        row(5, "tool", "1", "closedata", trace,
            "1111111111111111", None,
            "run", "end", "2026-10-20T00:00:08.040Z",
            {"turns": 3, "tokens": 42, "totals": {"took_ms": 40}},
            took=40, outcome="ok"),
    ]
    (OUT / "01.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    print("wrote corpus/closedata/01.jsonl")


if __name__ == "__main__":
    main()
