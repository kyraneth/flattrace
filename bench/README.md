# Writer overhead (synthetic, Linux)

`writers.py` compares **two checkouts** with identical adapters. It builds Rust
in release/offline/locked mode before timing, runs fresh Python/Node/Rust
processes, alternates baseline/candidate order by pass, and rotates language
order. All counts are required inputs: there are no workload/repeat defaults,
performance thresholds, automatic retries, CPU pinning or load gates.

Prepare the baseline checkout at the desired pre-optimization source, then copy
this `bench/` harness into it. For example, from the candidate checkout:

```sh
umask 002
git worktree add --detach ../writer-baseline BASELINE_REF
cp -a bench ../writer-baseline/
mkdir -p target
# Set CARGO_HOME to an existing offline cache if the default cache is unavailable.
nice -n 10 ionice -c 3 python3 bench/writers.py \
  --baseline-root ../writer-baseline --scratch target --out bench/comparison.json \
  --label comparison --iterations 20000 --disabled-iterations 20000000 \
  --activation-iterations 20000 --repeats 3
```

Output must be a new file. Choose an existing scratch directory on the filesystem
to measure; all temporary builds/traces are removed after the run, including on
failure. Do not keep raw traces for publication: environment writers' transient
run headers contain the actual host. Only aggregate allowlisted JSON reports
contain no host/user/directory/commit identity, but still retain aggregate
machine facts. Review and scrub those facts before any publication. Writer and
harness **content** SHA-256 digests identify the measured source without commit
IDs. Cargo diagnostics go to the terminal, never into the report. The runner
rejects differing timed adapters/lockfiles, diagnostics, disabled-mode files,
invalid envelopes, missing lines, sequence errors and unexpected payloads.
Run without Python `-O` (readback and disabled checks intentionally use asserts).

## What is timed

Each operation is a start, a summary point and an end with the same small nested
payload (`count`, `bytes`, synthetic label, numeric list, unknown null). Level 2
adds one debug point. This is an explicit **tool-side** level policy, not writer
filtering. Each write is flushed to the OS, never fsynced. Level 1 writes three
JSONL events per operation; level 2 writes four. Start clocks remain after the
start write. Startup, file creation, final run close, readback and Rust builds
are outside the timed loop. This measures synchronous steady-loop residence,
not CPU time, crash persistence or whole-product slowdown.

- `events_per_s`: operation events / timed loop seconds (not operations/sec).
- `bytes_per_event`: file-size delta during that loop / operation events. It
  excludes run-start/end, includes the newline, and reflects each writer's
  existing spacing/time/float representation. Small changes are timing-digit
  differences, not compression or a format change.
- `off`: directory unset/empty. `0`: directory present, level zero. Both have
  zero events/bytes. Enabled throughput and bytes/event are reported as
  **null**; the actual emitted event count and `event_bytes` are zero.
- Disabled `ns_per_iteration`: cached optional-writer branch plus loop cost,
  with no payload construction. Rust uses `black_box`; JavaScript may optimize
  away the known-null branch. This is a call-site floor, **not** a language or
  library ranking and not overhead subtracted from an uninstrumented control.
- `activation_ns_per_call`: repeated disabled `from_env`/`fromEnv` calls,
  measured separately for `off` and `0`; includes the adapter's empty argument
  setup/assertion. Enabled construction cost is not claimed.
- `peak_rss_KiB`: process high-water RSS, including runtime/setup; not allocation
  volume or just the timed loop. Driver RSS includes streamed readback.

Reports retain every sample and min/median/max, aggregate load averages,
CPU tick counters, CPU/I/O/memory pressure and available/swap memory before and
after each subprocess, plus priority, filesystem class and runtime versions.
These boundaries bracket process setup/activation as well as its timed loop;
they are not continuous monitoring. Driver readback streams one line at a time,
retaining only active span IDs, never the whole trace. Report size grows with
explicit repeats, not trace length. Builds run serially at inherited priority.

## Workload/repeat rationale for the example command

The example command retains calibrated synthetic workload counts, not universal
defaults or limits. Enabled operations amortize startup-scale effects; the cached
disabled branch needs more iterations because its tiny loop is timer/scheduler
sensitive. Disabled activation is measured independently, avoiding millions of
unnecessary environment parses. Choose counts for the workload being studied.
Three paired passes provide a central observation and an observed range, while
rotating each of the three languages through first position. They do **not**
estimate a confidence interval or establish stable speedup under contention.
Increasing repeats is explicit operator work; do not keep retrying until a
desired speedup appears.

Machine-specific retained results are not bundled with this public harness.
This is not a product workload or proof of product-integration performance.

## Tests

```sh
python3 -m unittest
python3 -m unittest discover -s bench -p 'test_*.py'
cargo test --offline --manifest-path crates/flattrace/Cargo.toml
node --test typescript/test/*.test.mjs
```

A paired run exercises all three real writer entry points and reads every
generated event. Unit tests additionally cover disabled cases,
readback corruption detection, caller immutability, non-finite values, sparse
arrays, special JSON keys, per-call flush visibility, and entropy ID encoding.
