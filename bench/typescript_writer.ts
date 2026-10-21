// SPDX-License-Identifier: MIT
// One fresh-process sample; all workload inputs supplied by writers.py.
import { statSync } from 'node:fs';
import { Writer } from '../typescript/writer.ts';

const [raw, mode, directory, activationRaw] = process.argv.slice(2);
const iterations = Number(raw);
const activationIterations = Number(activationRaw);
delete process.env.FLATTRACE_TRACEPARENT;
process.env.FLATTRACE_DIR = mode === 'off' ? '' : directory;
process.env.FLATTRACE_LEVEL = mode === 'off' ? '1' : mode;
const writer = Writer.fromEnv('writer-bench', 'synthetic');
const payload = { count: 1, bytes: 128, label: 'synthetic', values: [1, 2, 3], unknown: null };
const before = writer ? statSync(writer.path).size : 0;
const started = process.hrtime.bigint();
for (let i = 0; i < iterations; ++i) {
  if (writer !== null) {
    const span = writer.start('operation', payload);
    writer.point('summary', payload, span);
    if (writer.level >= 2) writer.point('debug', payload, span);
    writer.end(span, 'ok', payload);
  }
}
const elapsed = Number(process.hrtime.bigint() - started) / 1e9;
const size = writer ? statSync(writer.path).size - before : 0;
let activation: number | null = null;
if (writer) writer.close();
else {
  const start = process.hrtime.bigint();
  for (let i = 0; i < activationIterations; ++i) {
    if (Writer.fromEnv('writer-bench', 'synthetic') !== null) throw Error('unexpected writer');
  }
  activation = Number(process.hrtime.bigint() - start) / activationIterations;
}
console.log(JSON.stringify({ elapsed_s: elapsed, event_bytes: size, activation_ns_per_call: activation, peak_rss_KiB: process.resourceUsage().maxRSS }));
