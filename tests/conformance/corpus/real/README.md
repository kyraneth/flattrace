# Real sample (reviewed redacted export)

`01.jsonl` (39 events, complete) is an agent-session trace exported with the
reviewed route:

```sh
python3 -m flattrace export <run> --out <fresh-dir>
```

Export substituted every host, path and user token consistently (`<host-N>`
placeholders) and masks credential-looking text. The full redacted text was
read before admission: no host names, user names, absolute paths or error
prose with private names remain. Kept deliberately: `tool`/`tool_version`,
`agent.name` and harness facts in `env` (agent/model/session join keys),
per-event float `took_ms` values at full precision, `eventloop.window` stall
windows with `stats` payloads (the stall-overlap shape), and nested
session refresh/snapshot spans. Random run/trace/span IDs and the opaque
session ID are structural join keys, not identities.

The former second sample was removed because its event names were not suitable
for publication; reviewed exports are not hand-edited. Unfinished-run coverage
remains in the synthetic `../c04-unfinished.jsonl` fixture.

Do not hand-edit this file's trace. If it ever needs replacing, re-export and
re-review; the goldens in `../golden/` do not cover this directory, and
`test_conformance.py` pins only its check/digest shape, not exact bytes.
