# Exported compatibility and synthetic fixtures

All committed JSONL here passed through `python3 -m flattrace export`.
They are deliberately synthetic, not real product measurements.

- `legacy/1.jsonl`: generated with the earlier v1 Python writer, whose source
  SHA-256 was `b85e71147e75d277f842f0764c826e70e2e1d9c87660ddcf39a3fb8c1c396b22`.
  Only clocks, ID entropy and hostname were mocked. It lacks seq, pid, command,
  environment and learning fields; tests require continued v1 acceptance.
- `synthetic/1.jsonl`: agent parent; `2.jsonl`: nested summary;
  `3.jsonl`: benchmark target; `4.jsonl`: separate explicit verdict.
  `5.jsonl` is an unfinished pack fixture; `5.tail.txt` records an omitted
  synthetic torn final line. Checking the full bundle intentionally exits 1.

Synthetic task/config hashes name the bytes `synthetic-scene-fps-task` and
`synthetic-candidate-config` respectively, using SHA-256. These invented
measurements exercise joins and redaction only. Actual product examples live
under [examples/oxblend](../../examples/oxblend/README.md).
