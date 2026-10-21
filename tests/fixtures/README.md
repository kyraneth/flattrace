# Compatibility and parity fixtures

`legacy/1.jsonl` is a frozen synthetic v1 writer output. Its SHA-256 is
`8dfaa5fc537ef58cf0562f61dc170fe6963b5cdf48fbdfd64d5d9dfc0e93824d`.
It deliberately lacks seq, pid, command, environment and learning fields.
Keep it byte-identical: check, event coverage and export must accept it without
adding fields, including explicit CLI command selection.

`sanitize-argv.json` supplies the same synthetic secret-flag cases to Python,
Rust and TypeScript tests. Never add real credentials, private paths or hosts.

The public fixture is a reviewed redacted export of that frozen synthetic output.

`synthetic/1.jsonl` is the agent parent; `2.jsonl` its nested summary;
`3.jsonl` a benchmark target; `4.jsonl` its separate verdict. `5.jsonl` is
unfinished and `5.tail.txt` records its omitted torn tail. These invented
timings demonstrate joins, not performance; measured examples are under
[examples/oxblend](../../examples/oxblend/README.md).
