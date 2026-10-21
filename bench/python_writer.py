# SPDX-License-Identifier: MIT
"""One fresh-process sample. Orchestrated by writers.py; no workload defaults."""
import json
import os
import resource
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flattrace import Writer

iterations, mode, directory = int(sys.argv[1]), sys.argv[2], sys.argv[3]
activation_iterations = int(sys.argv[4])
os.environ.pop("FLATTRACE_TRACEPARENT", None)
os.environ["FLATTRACE_DIR"] = "" if mode == "off" else directory
os.environ["FLATTRACE_LEVEL"] = "1" if mode == "off" else mode
writer = Writer.from_env("writer-bench", "synthetic")
payload = {"count": 1, "bytes": 128, "label": "synthetic", "values": [1, 2, 3], "unknown": None}
before = writer.path.stat().st_size if writer else 0
started = time.perf_counter_ns()
for _ in range(iterations):
    if writer is not None:
        span = writer.start("operation", payload)
        writer.point("summary", payload, span=span)
        if writer.level >= 2:
            writer.point("debug", payload, span=span)
        writer.end(span, "ok", payload)
elapsed = (time.perf_counter_ns() - started) / 1e9
size = writer.path.stat().st_size - before if writer else 0
activation = None
if writer is not None:
    writer.close()
else:
    started = time.perf_counter_ns()
    for _ in range(activation_iterations):
        assert Writer.from_env("writer-bench", "synthetic") is None
    activation = (time.perf_counter_ns() - started) / activation_iterations
print(json.dumps({"elapsed_s": elapsed, "event_bytes": size, "activation_ns_per_call": activation,
                  "peak_rss_KiB": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
