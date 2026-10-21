# Implementation status

flattrace v1 provides synchronous Python, Rust (Linux) and TypeScript writers,
a checker, a redacting export command, streaming corpus stats and a JSON Schema. Code and docs are MIT.

Oxblend uses the Rust writer. [Reviewed example exports](examples/oxblend/README.md)
show real public-corpus operations, Python parent-context propagation, and an
interrupted pack with genuinely missing ends. These are operational examples,
not a benchmark or a claim that every Oxblend branch is represented here.

The envelope accepts additive fields. Legacy v1 traces without sequence numbers
or newer optional payload fields remain readable. Tests cover writer lifecycle,
parentage, sink failures, redaction, torn tails and explicit verdict joins.
Audit fixes align root-span guards, payload degradation, typed API boundaries,
Unicode handling and credential masking across the writers. Only close/finish
ends the root; malformed structure remains an API error. Flush is not crash
persistence, and sequence continuity does not prove a complete retained suffix.

[SPEC.md](SPEC.md) distinguishes normative MUST/SHOULD requirements from rationale.
Optional [learning conventions](docs/learning.md) live outside the core format.
The streaming `stats` reader uses disk-backed exact quantiles, reports invalid and
unfinished evidence, and avoids claiming exclusive CPU time for overlapping or
cross-process spans. The [synthetic writer overhead harness](bench/README.md)
provides explicit paired workload measurements, not a stable speedup claim.

There is no uploader, collector, journal exporter, viewer, rotation or automatic
retention. Tools own event names and payload meanings. Upload consent and
analysis are separate concerns; absence of a trace does not prove no work ran.

Registry manifests are preparation only: Rust publishing and npm publication
remain disabled. The Python project metadata describes a source-installable
package; no package has been published as part of repository preparation.
