#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build a larger deterministic synthetic corpus and time the Python and Rust
stats readers over it. Single measured runs, reported as-is: no repeats,
thresholds or gates. Usage: reader_bench.py OUT_DIR [FLATTRACE_READ_BIN].
Keep OUT_DIR outside the repo.
"""
import gzip
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

TRACE = "f" * 31 + "1"


def build_corpus(out, files=40, spans_per_file=400, seed=7):
    rng = random.Random(seed)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    events = 0
    counter = [0]

    def new_span(f):
        counter[0] += 1
        return f"{(f * 1000000 + counter[0]) % (16 ** 16):016x}"

    for f in range(files):
        run = f"bench-{f:03d}"
        trace = f"{f + 1:032x}"
        root = f"{(f + 10 ** 12) % (16 ** 16):016x}"
        rows = [{
            "v": 1, "seq": 0, "tool": "bench", "tool_version": "1",
            "run": run, "trace": trace, "span": root, "parent": None,
            "event": "run", "state": "start", "t": "2026-10-26T00:00:00.000Z",
            "data": {"argv": ["bench"], "host": "synthetic", "version": "1"},
        }]
        seq = 1
        stack = []
        closed = 0
        while closed < spans_per_file or stack:
            if stack and (closed >= spans_per_file or rng.random() < 0.45):
                span, parent, event, took = stack.pop()
                rows.append({
                    "v": 1, "seq": seq, "tool": "bench", "tool_version": "1",
                    "run": run, "trace": trace, "span": span, "parent": parent,
                    "event": event, "state": "end",
                    "t": "2026-10-26T00:00:00.100Z", "data": {},
                    "took_ms": took,
                    "outcome": "error" if rng.random() < 0.05 else "ok",
                })
                seq += 1
                closed += 1
                continue
            span = new_span(f)
            parent = stack[-1][0] if stack else root
            event = rng.choice(["render", "encode", "io.load", "tool.call"])
            took = rng.choice([1, 5, 20, 100, 500])
            rows.append({
                "v": 1, "seq": seq, "tool": "bench", "tool_version": "1",
                "run": run, "trace": trace, "span": span, "parent": parent,
                "event": event, "state": "start",
                "t": "2026-10-26T00:00:00.000Z",
                "data": {"shot": f"sh{rng.randrange(20):03d}"},
            })
            seq += 1
            if rng.random() < 0.02:
                rows.append({
                    "v": 1, "seq": seq, "tool": "bench", "tool_version": "1",
                    "run": run, "trace": trace, "span": span, "parent": parent,
                    "event": "failure", "state": "point",
                    "t": "2026-10-26T00:00:00.000Z", "data": {"count": 3},
                    "error": {"kind": "io", "message": "synthetic bench failure"},
                })
                seq += 1
            stack.append((span, parent, event, took))
        rows.append({
            "v": 1, "seq": seq, "tool": "bench", "tool_version": "1",
            "run": run, "trace": trace, "span": root, "parent": None,
            "event": "run", "state": "end", "t": "2026-10-26T00:00:01.000Z",
            "data": {}, "took_ms": 1000, "outcome": "ok",
        })
        path = out / f"bench-{f:03d}.jsonl"
        if f == 0:
            with gzip.open(str(path) + ".gz", "wt", encoding="utf-8") as stream:
                for row in rows:
                    stream.write(json.dumps(row) + "\n")
        else:
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        events += len(rows)
    total_bytes = sum(p.stat().st_size for p in out.iterdir())
    return files, total_bytes, events


def timed(command, cwd):
    start = time.perf_counter()
    proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    return time.perf_counter() - start, proc


def main():
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("usage: reader_bench.py OUT_DIR [FLATTRACE_READ_BIN]", file=sys.stderr)
        raise SystemExit(2)
    out = Path(sys.argv[1])
    binary = sys.argv[2] if len(sys.argv) > 2 else None
    files, total_bytes, events = build_corpus(out)
    inputs = sorted(str(p) for p in out.iterdir())
    python_seconds, python_proc = timed(
        [sys.executable, "-m", "flattrace", "stats", "--json", *inputs], ".")
    print(f"synthetic corpus: {files} files, {total_bytes} bytes, {events} events")
    print(f"python stats: {python_seconds:.2f} s exit={python_proc.returncode} "
          f"stdout_bytes={len(python_proc.stdout.encode())}")
    if binary:
        rust_seconds, rust_proc = timed([binary, "stats", "--json", *inputs], ".")
        print(f"rust stats:   {rust_seconds:.2f} s exit={rust_proc.returncode} "
              f"stdout_bytes={len(rust_proc.stdout.encode())}")
        if python_proc.returncode == 0 and rust_proc.returncode == 0:
            print(f"ratio python/rust: {python_seconds / rust_seconds:.2f}")


if __name__ == "__main__":
    main()
