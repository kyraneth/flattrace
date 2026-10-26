# Real samples (reviewed redacted exports)

`01.jsonl` (39 events, complete) and `02.jsonl` (29 events, unfinished: its
writer was still running) are two Zamil session traces from the current
development machine, exported with the reviewed route:

```sh
python3 -m flattrace export <run-a> <run-b> --out <fresh-dir>
```

Export substituted every host, path and user token consistently (`<host-N>`
placeholders) and masks credential-looking text. The full redacted text was
read before admission: no host names, user names, absolute paths or error
prose with private names remain. Kept deliberately: `tool`/`tool_version`,
`agent.name` and harness facts in `env` (agent/model/session join keys),
per-event float `took_ms` values at full precision, `eventloop.window` stall
windows with `stats` payloads (the stall-overlap shape), nested
session refresh/snapshot spans, and one unfinished run. Random run/trace/span
IDs and the opaque session ID are structural join keys, not identities.

Do not hand-edit these files. If they ever need replacing, re-export and
re-review; the goldens in `../golden/` do not cover this directory, and
`test_conformance.py` pins only its check/digest shape, not exact bytes.
