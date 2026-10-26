# Run-end data fixture

`01.jsonl` is a complete synthetic run whose root end carries a `data`
payload (run totals), exercising the close-data writer API
(`close(data=...)` / `finish_with`) on the reader side. The committed golden
`../golden/spans_runend.json` pins the paired rows, including the run row's
`end_data`. Regenerate inputs with
`python3 tests/conformance/make_closedata.py` (byte-deterministic) and goldens
with `python3 tests/conformance/make_goldens.py`.
