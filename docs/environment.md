# Environment and build facts

Recommended, not required: record known facts in `data.env` on run start.
Use null for a reported-but-unknown value; omit facts the tool does not report.
Do not dump an environment or credentials. Export also redacts this object.

| Key | Meaning |
|---|---|
| `host.name`, `host.arch` | Hostname and architecture |
| `os.type`, `os.version`, `os.kernel.version` | OS family, release and kernel |
| `host.cpu.model.name` | Known CPU model |
| `service.name`, `service.version` | Tool name and version |
| `process.runtime.name`, `process.runtime.version` | Runtime name and version |
| `tool.build.commit`, `tool.binary.sha256` | Known source revision and executable digest |
| `libraries` | Known `{name, version}` dependency objects |
| `libraries.lockfile.sha256` | Optional alternative to a dependency inventory |
| `agent.name`, `agent.version` | Agent identity/version when applicable |
| `harness.name`, `harness.version` | Execution harness identity/version |
| `gen_ai.provider.name`, `gen_ai.request.model` | Requested provider and model |
| `model.requested_effort` | Requested reasoning setting, not acceptance proof |

OpenTelemetry resource names are reused where meanings match. Kernel, build,
library, agent, harness and requested-effort keys are steptrace conventions,
not a claim that OpenTelemetry defines them. No machine scan or hashing is
required. Record effective model/settings changes on the corresponding spans.
A run header cannot represent all later changes. Unknown accounting is null,
not zero; never sum overlapping parent/child measurements.

Tools supply sanitized argv explicitly; it is empty when absent. Writers mask
common secret flags too, but cannot recognize every tool-specific credential.
For Rust floating-point capture, use `set_number` before converting to a JSON
value: serde_json otherwise loses the distinction between non-finite and unknown
null. Python/TypeScript normalize directly, adding a note for non-finite values.
