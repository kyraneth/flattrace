# Examples and offline learning

[Oxblend 1.3.0](oxblend/README.md) supplies the real exported command examples.
Only public-corpus inputs were used; no project assets or raw reports are bundled.

`learning.py` joins completed runs to explicit verdicts, deduplicating by
`(trace, run)`. It emits task/variant, comparability digests, correctness, named
score, separate reward, grader identity and measured duration. It does not train
a model, infer rewards, invent missing measurements or equate recording end with
successful task completion. Unfinished/malformed inputs are excluded.

A small [synthetic exported fixture bundle](../tests/fixtures/synthetic/) tests
these joins. It is deliberately separate from measured product examples:

```sh
python3 examples/learning.py tests/fixtures/synthetic/[1-4].jsonl
python3 -m flattrace check tests/fixtures/synthetic/5.jsonl # expected exit 1
```

A hill-climber must compare matching task/config identities and graders, not
labels alone; failure is not a fast success. No performance claim follows from
synthetic timings or the operational examples. See the extracted
[learning conventions](../docs/learning.md) for optional comparability identities,
grading/reward limits and the synthetic fixtures' hash inputs; these conventions
do not change [the core format](../SPEC.md).
