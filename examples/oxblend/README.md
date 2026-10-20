# Real Oxblend 1.3.0 traces

Freshly recorded on 2026-10-20 using public Blender demo files only, then passed
through `python3 -m steptrace export`. Paths and hosts are placeholders; fresh
trace/run/span IDs, parentage, timings, outcomes and unfinished evidence remain.
No source `.blend`, texture, raw trace or stdout report is redistributed here.
The executable identifies as Oxblend 1.3.0, SHA-256
`20a446a4a8510612ccae55e95b05c47c9f05fb3d5bb11498f587501336098950`.

| Bundle | Actual operation | Expected checker result |
|---|---|---|
| [summary](summary/1.jsonl) | `summary blender-5.2-splash.blend` | 0 |
| [trace](trace/1.jsonl) | `trace udim-monster.blend` | 0 |
| [pack](pack/1.jsonl) | Complete UDIM project directory pack: 23/23 members published | 0 |
| [killed-pack](killed-pack/1.jsonl) | Another UDIM pack, stopped with SIGKILL after first member publication | 1 (unfinished) |
| [nested parent](nested/1.jsonl), [child](nested/2.jsonl) | Python writer's `tool.call` passes `STEPTRACE_TRACEPARENT` to a real Oxblend summary | 0 |

The complete pack's report had `complete:true`, `planning_complete:true`, no
errors and every planned member written. Publication did not request durability;
completion is not an fsync guarantee. These records are not a benchmark.

## Check and understand

From the repository root:

```sh
python3 -m steptrace check --events examples/oxblend/steptrace-events.json examples/oxblend/summary/1.jsonl examples/oxblend/trace/1.jsonl examples/oxblend/pack/1.jsonl
python3 -m steptrace check examples/oxblend/nested/*.jsonl
python3 -m steptrace check --events examples/oxblend/steptrace-events.json examples/oxblend/nested/2.jsonl
python3 -m steptrace check examples/oxblend/killed-pack/1.jsonl # expected exit 1
```

[steptrace-events.json](steptrace-events.json) is Oxblend 1.3.0's tool-owned event
contract, not a synthetic draft. The Python parent has its own event names, so
check that file without the Oxblend-only contract.

The interrupted trace records trusted planning, execution and an actual
`publish` end with `output_written:true`, then starts the next write without
finishing it. The outer run, command and active operations have no invented
ends. This is an unfinished tail of complete JSON lines, not an artificially
torn line: no `.tail.txt` was needed. The native process receipt established
SIGKILL; missing trace ends alone cannot establish a signal or an exclusive cause.
Do not interpret one published member as a complete portable project, or resume
into the partially populated destination.

## Minimal nested producer

With Oxblend 1.3.0 on PATH and `STEPTRACE_DIR` set, run this from the checkout:

```python
import subprocess
from steptrace import Writer

w = Writer.from_env('python-example', '1', argv=['summary-public-scene'])
span = w.start('tool.call', {'tool': 'oxblend', 'command': 'summary'}) if w else None
result = subprocess.run(['oxblend', 'summary', 'public-scene.blend'],
                        env=w.child_env(span) if w else None)
if w:
    outcome = 'ok' if result.returncode == 0 else 'error'
    w.end(span, outcome, {'exit_code': result.returncode})
    w.close(outcome)
```

The child run shares the parent trace and names the parent's `tool.call` span
as its parent, while owning a distinct run/file. Join by those IDs, never by
wall-time order. Export parent and child together to share a consistent map.
