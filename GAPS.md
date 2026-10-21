# Real-trace analysis gaps (proposals only)

No envelope, writer guarantee or SPEC change is made here.

- **Exact async/cross-process self time.** Parentage plus local `took_ms` cannot
  locate overlapping child intervals on a shared monotonic timeline. Wall clocks
  are correlation only. Current stats subtracts only sequential, contained local
  direct children and otherwise reports unknown. Propose an explicit clock-domain
  and monotonic endpoint convention only if real workloads need exact unions;
  cross-process attribution still needs an honest alignment/uncertainty policy.
- **Warnings/severity.** v1 defines structured errors and outcomes, not warnings.
  Stats recognizes explicit payload `severity` strings without guessing names or
  prose. Propose one documented payload convention for warning/error severity and
  whether coalesced counts represent occurrences. This is not an envelope field.
- **Partial/duplicate acquisitions.** `(trace, run)` deduplicates exports, but the
  format does not say which of two divergent/truncated copies is authoritative.
  Stats lists skipped duplicates and uses first-copy wins, never merges endings.
  Propose an acquisition/integrity policy outside the line shape if corpus
  collection regularly mixes growing originals and exports.
- **Comparability and workload meaning.** Provenance/task/config hashes and
  payload units are optional; event names may change across tool versions.
  Aggregation by name is descriptive, not proof that unlike runs are comparable.
  Propose tool-owned event/metric/version and benchmark selection conventions
  backed by actual test traces, including unknown/missing provenance.
- **Instrumentation attribution.** `took_ms` excludes its own start/end writes,
  but includes nested tracing, waits and scheduling. It cannot yield CPU time,
  I/O wait or uninstrumented counterfactual speed. Keep overhead benchmarks
  separate; propose additional payload measurements only for demonstrated needs.

Real product test/benchmark corpora should drive the next changes. Synthetic
fixtures prove analysis behavior, not product-integration or performance claims.
