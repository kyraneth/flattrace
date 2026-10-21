#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Linux shared-host writer benchmark: explicit workload, no timing thresholds."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flattrace.check import validate_event


def positive(raw):
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def context():
    """Allowlisted aggregate machine context, never host/user/path/env dumps."""
    pressure = {}
    for kind in ("cpu", "io", "memory"):
        path = Path("/proc/pressure") / kind
        if path.exists():
            pressure[kind] = path.read_text().strip().splitlines()
    cpu = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        if key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
            memory[key + "_KiB"] = int(value.split()[0])
    return {"utc": datetime.now(timezone.utc).isoformat(), "loadavg": list(os.getloadavg()),
            "cpu_ticks": list(map(int, cpu)), "pressure": pressure, "memory": memory}


def verify(directory, iterations, mode):
    """Untimed readback: every generated line, seq, payload and span pairing."""
    files = list(directory.iterdir())
    if mode in ("off", "0"):
        assert not files, "disabled writer created a file"
        return 0
    assert len(files) == 1
    active = {}
    count = 0
    first = None
    last = None
    with files[0].open("rb") as stream:
        for count, line in enumerate(stream, 1):
            assert line.endswith(b"\n"), "line not flushed/terminated"
            row = json.loads(line)
            validate_event(row)
            assert row["seq"] == count - 1
            if first is None:
                first = row
                assert (row["event"], row["state"]) == ("run", "start")
            assert all(row[key] == first[key] for key in ("run", "trace", "tool", "tool_version"))
            last = row
            if row["state"] == "start":
                assert row["span"] not in active
                if row["event"] != "run":
                    assert row["parent"] in active
                else:
                    assert row["data"]["level"] == int(mode)
                active[row["span"]] = (row["event"], row["parent"])
            else:
                assert row["span"] in active
                assert row["parent"] == active[row["span"]][1]
                if row["state"] == "end":
                    assert active[row["span"]] == (row["event"], row["parent"])
                    del active[row["span"]]
            if row["event"] != "run":
                assert row["data"] == {"count": 1, "bytes": 128, "label": "synthetic",
                                       "values": [1, 2, 3], "unknown": None}
    assert not active
    expected = iterations * (4 if mode == "2" else 3)
    assert count == expected + 2
    assert (last["event"], last["state"]) == ("run", "end")
    assert (last["span"], last["parent"]) == (first["span"], first["parent"])
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=positive, required=True, help="synthetic operations per sample")
    parser.add_argument("--disabled-iterations", type=positive, required=True, help="cached disabled branch iterations")
    parser.add_argument("--activation-iterations", type=positive, required=True, help="disabled from_env calls per sample")
    parser.add_argument("--repeats", type=positive, required=True, help="independent fresh-process paired passes")
    parser.add_argument("--label", choices=("pilot", "comparison"), required=True)
    parser.add_argument("--baseline-root", type=Path, required=True, help="baseline checkout with identical bench harness")
    parser.add_argument("--scratch", type=Path, required=True, help="existing local filesystem for temporary traces/builds")
    parser.add_argument("--out", type=Path, required=True, help="new publishable JSON report; never raw traces")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("--out must not exist")
    if not args.scratch.is_dir():
        parser.error("--scratch must be an existing directory")
    baseline = args.baseline_root.resolve()
    harness_files = ("bench/python_writer.py", "bench/typescript_writer.ts", "bench/rust/src/main.rs",
                     "bench/rust/Cargo.toml", "bench/rust/Cargo.lock")
    for path in harness_files:
        if (baseline / path).read_bytes() != (ROOT / path).read_bytes():
            parser.error("baseline and candidate need identical benchmark adapters and lockfile")
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    writer_paths = {"python": "flattrace/writer.py", "rust": "crates/flattrace/src/lib.rs", "typescript": "typescript/writer.ts"}
    source_hashes = {variant: {language: digest(root / path) for language, path in writer_paths.items()}
                     for variant, root in (("baseline", baseline), ("candidate", ROOT))}
    env = dict(os.environ)
    # The caller selects CARGO_HOME if an offline cache is needed.
    with tempfile.TemporaryDirectory(prefix="writer-bench-", dir=args.scratch) as build:
        commands = {}
        for variant, root in (("baseline", args.baseline_root.resolve()), ("candidate", ROOT)):
            env["CARGO_TARGET_DIR"] = str(Path(build) / variant)
            subprocess.run(["cargo", "build", "--offline", "--release", "--locked", "--manifest-path",
                            str(root / "bench/rust/Cargo.toml")], cwd=root, env=env, check=True,
                           stdout=sys.stderr)
            commands[variant] = {"python": [sys.executable, str(root / "bench/python_writer.py")],
                                 "rust": [str(Path(build) / variant / "release/flattrace-writer-bench")],
                                 "typescript": ["node", str(root / "bench/typescript_writer.ts")]}
        languages = list(commands["candidate"])
        def version(command):
            return subprocess.check_output(command, text=True).strip()
        metadata = {"schema": 1, "label": args.label, "iterations": args.iterations,
                    "disabled_iterations": args.disabled_iterations, "activation_iterations": args.activation_iterations,
                    "repeats": args.repeats, "cpu_count": os.cpu_count(), "arch": platform.machine(),
                    "system": platform.system(), "python": platform.python_version(),
                    "node": version(["node", "--version"]),
                    "rustc": version(["rustc", "--version"]).split()[1],
                    "filesystem": version(["stat", "-f", "-c", "%T", str(args.scratch)]),
                    "nice": os.getpriority(os.PRIO_PROCESS, 0),
                    "ionice": version(["ionice", "-p", str(os.getpid())]),
                    "timing": "in-process operation loop; setup, close, readback and build excluded",
                    "flush": "each line to OS, no fsync", "before": context(),
                    "writer_source_sha256": source_hashes,
                    "harness_source_sha256": {path: digest(ROOT / path) for path in (*harness_files, "bench/writers.py")}}
        configuration = {"iterations": args.iterations, "disabled_iterations": args.disabled_iterations,
                         "activation_iterations": args.activation_iterations, "repeats": args.repeats,
                         "payload": {"count": 1, "bytes": 128, "label": "synthetic", "values": [1, 2, 3], "unknown": None}}
        metadata["configuration_sha256"] = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
        samples = []
        for repeat in range(args.repeats):
            # Rotate language order to reduce fixed order bias across repeats.
            offset = repeat % len(languages)
            for language in languages[offset:] + languages[:offset]:
                for mode in ("off", "0", "1", "2"):
                    variants = ("baseline", "candidate") if repeat % 2 == 0 else ("candidate", "baseline")
                    iterations = args.disabled_iterations if mode in ("off", "0") else args.iterations
                    for variant in variants:
                        with tempfile.TemporaryDirectory(prefix="writer-sample-", dir=args.scratch) as tmp:
                            before = context()
                            proc = subprocess.run([*commands[variant][language], str(iterations), mode, tmp,
                                                   str(args.activation_iterations)], cwd=ROOT, env=env,
                                                  capture_output=True, text=True, check=True)
                            after = context()
                            # Unavailable sinks must not look fast.
                            if proc.stderr:
                                raise RuntimeError("writer emitted diagnostics; sample refused")
                            sample = json.loads(proc.stdout)
                            events = verify(Path(tmp), iterations, mode)
                            sample.update({"variant": variant, "language": language, "mode": mode,
                                           "repeat": repeat + 1, "events": events, "before": before, "after": after,
                                           "ns_per_iteration": sample["elapsed_s"] * 1e9 / iterations,
                                           "events_per_s": events / sample["elapsed_s"] if events else None,
                                           "bytes_per_event": sample["event_bytes"] / events if events else None})
                            samples.append(sample)
                            print(f"{variant} {language} {mode} pass {repeat+1}: "
                                  f"{sample['ns_per_iteration']:.1f} ns/op, {sample['events_per_s']} events/s", file=sys.stderr)
        metadata["after"] = context()
        metadata["driver_peak_rss_KiB"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        summaries = []
        for variant in commands:
            for language in languages:
                for mode in ("off", "0", "1", "2"):
                    matching = [s for s in samples if s["variant"] == variant and s["language"] == language and s["mode"] == mode]
                    summary = {"variant": variant, "language": language, "mode": mode}
                    for key in ("ns_per_iteration", "events_per_s", "bytes_per_event", "activation_ns_per_call", "peak_rss_KiB"):
                        values = [s[key] for s in matching if s[key] is not None]
                        summary[key] = ({"min": min(values), "median": statistics.median(values), "max": max(values)}
                                        if values else None)
                    summaries.append(summary)
        with args.out.open("x") as stream:
            json.dump({"metadata": metadata, "summaries": summaries, "samples": samples}, stream, indent=2)
            stream.write("\n")


if __name__ == "__main__":
    main()
