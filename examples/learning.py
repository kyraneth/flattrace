# SPDX-License-Identifier: MIT
"""Join completed runs to explicit grader observations; no training algorithm."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flattrace.check import read_trace


def observations(files):
    runs, verdicts = {}, []
    for path in files:
        rows, issues = read_trace(path)
        if issues:
            continue  # Unfinished/malformed records never become successful rewards.
        first, last = rows[0], rows[-1]
        key = (first["trace"], first["run"])
        if key in runs:
            continue  # Re-reading/re-exporting one run must not duplicate its grades.
        runs[key] = {**first["data"], "took_ms": last["took_ms"], "outcome": last["outcome"]}
        verdicts.extend(row["data"] for row in rows if row["event"] == "verdict" and row["state"] == "point")
    result = []
    for verdict in verdicts:
        key = (verdict["trace"], verdict["run"])
        target = runs.get(key)
        if (target is not None and "task" in target and "variant" in target
                and target["outcome"] not in ("cancelled", "unknown")):
            result.append({"trace": key[0], "run": key[1],
                           **{k: target[k] for k in ("task", "variant", "took_ms", "outcome")},
                           **{k: target.get(k) for k in ("episode", "attempt", "seed", "task_hash", "config_hash")},
                           **{k: verdict.get(k) for k in ("name", "correct", "score", "reward", "reason", "grader")}})
    return result


if __name__ == "__main__":
    print(json.dumps(observations(sys.argv[1:]), indent=2, allow_nan=False))
