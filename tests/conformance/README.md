# Conformance corpus and goldens

`corpus/` holds the frozen input set the reader engine must handle:

- `c01-parent.jsonl` / `c02-child.jsonl` — one trace across two processes:
  the child run parents to the parent's `tool.call` span, with `agent.name`,
  model and session facts for the named filters.
- `c03-async.jsonl` — an async child outliving its launcher plus overlapping
  children (unknown-self reasons).
- `c04-unfinished.jsonl` — unfinished run with inactive parent, unmatched
  end, stray point and unfinished spans.
- `c05-dup-a.jsonl` / `c05-dup-b.jsonl` — byte-identical duplicates;
  the first path wins, the second is excluded from samples.
- `c06-torn.jsonl` — truncated final line with no newline.
- `c07-payload.jsonl` — shot/file subject keys, error objects with `where`,
  a coalesced error point with `count`, and a stall window overlapping
  `tool.exec` spans.
- `c08-baseline.jsonl` / `c09-candidate.jsonl` — comparable runs with
  verdict points for `compare`.
- `c10-archive.jsonl.gz` — gzip member.
- `c11-bad.jsonl` — interior invalid lines (bad JSON, bad envelope).
- `real/` — two reviewed redacted agent-session exports (see its README).

`agent-events.json` is the tool-owned contract for `--events` checks.

Regenerate inputs with `python3 tests/conformance/make_corpus.py`
(byte-deterministic) and goldens with
`python3 tests/conformance/make_goldens.py`, then review the diff.
`reference.py` is the test-only pure-Python contract implementation;
`golden/` outputs are frozen. Never hand-edit `golden/`.
