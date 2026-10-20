// SPDX-License-Identifier: MIT
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, readdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { spawnSync, spawn } from 'node:child_process';
import { once } from 'node:events';
import { Writer, context } from '../writer.ts';

const root = fileURLToPath(new URL('../../', import.meta.url));
const moduleUrl = new URL('../writer.ts', import.meta.url).href;
function fixture(t) {
  mkdirSync(join(root, 'target'), { recursive: true });
  const dir = mkdtempSync(join(root, 'target/node-'));
  t.after(() => rmSync(dir, { recursive: true }));
  return dir;
}
function run(code, env = process.env) {
  return spawnSync(process.execPath, ['--input-type=module', '-e',
    `import {Writer} from ${JSON.stringify(moduleUrl)};\n${code}`], { cwd: root, env, encoding: 'utf8' });
}
function check(path, ...args) {
  return spawnSync('python3', ['-m', 'steptrace', 'check', path, ...args], { cwd: root, encoding: 'utf8' });
}
function rows(path) { return readFileSync(path, 'utf8').trim().split('\n').map(JSON.parse); }

test('writer flushes, pairs spans, errors and learning data pass the common checker', t => {
  const path = join(fixture(t), 'run.jsonl');
  const w = new Writer(path, 'oxblend', 'dev', { argv: ['summary'], data: { command: 'summary', task: 'frames', variant: 'candidate' } });
  assert.equal(rows(path).length, 1);
  assert.equal(rows(path)[0].data.pid, process.pid);
  const command = w.start('summary');
  assert.throws(() => w.close(), /local spans still active/);
  w.point('count', { bytes: 0, unknown: null }, command);
  w.end(command, 'refused', {}, { kind: 'input', message: 'refused' });
  w.close('refused');
  assert.equal(check(path).status, 0);
  const data = rows(path);
  assert.equal(data[0].data.task, 'frames');
  assert.equal(data[3].span, command);
  assert.equal(data[3].outcome, 'refused');
  assert.ok(data[3].took_ms >= 0);
  assert.equal(data[2].data.unknown, null);
  assert.throws(() => new Writer(path, 'test', '1'), /EEXIST/);
});

test('cross-process parentage and flags survive the environment entry point', t => {
  const dir = fixture(t);
  const parentPath = join(dir, 'agent.jsonl');
  const w = new Writer(parentPath, 'agent', '1');
  const turn = w.start('agent.turn');
  const result = run(`const w = Writer.fromEnv('oxblend','1'); console.log(w.traceparent()); w.close();`,
    { ...w.childEnv(turn), STEPTRACE_DIR: join(dir, 'child'), STEPTRACE_TRACEPARENT: w.traceparent(turn).slice(0, -2) + '01' });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout.trim(), /-01$/);
  const name = readdirSync(join(dir, 'child'))[0];
  assert.match(name, /^\d{8}T\d{6}\.\d{3}Z-oxblend-[0-9a-f]{32}\.jsonl$/);
  const childPath = join(dir, 'child', name);
  const child = rows(childPath)[0];
  assert.equal(child.trace, w.trace);
  assert.equal(child.parent, turn);
  assert.notEqual(child.run, w.run);
  assert.ok(name.endsWith(`${child.run}.jsonl`));
  assert.equal(check(childPath).status, 0);
  w.end(turn, 'ok'); w.close();
});

test('off does no I/O, invalid context fails, unwritable location warns once and product continues', t => {
  const dir = fixture(t);
  const blocked = join(dir, 'blocked');
  writeFileSync(blocked, 'not a directory');
  const env = { ...process.env, STEPTRACE_DIR: '', STEPTRACE_TRACEPARENT: 'bad' };
  assert.equal(run(`if (Writer.fromEnv('tool','1') !== null) throw Error('enabled');`, env).status, 0);
  env.STEPTRACE_DIR = blocked;
  delete env.STEPTRACE_TRACEPARENT;
  const result = run(`Writer.fromEnv('tool','1'); Writer.fromEnv('tool','1'); console.log('product completed');`, env);
  assert.equal(result.status, 0);
  assert.equal(result.stdout.trim(), 'product completed');
  assert.equal(result.stderr.match(/steptrace: tracing disabled/g).length, 1);
  assert.throws(() => context('bad'), /invalid/);
  assert.throws(() => context(`00-${'0'.repeat(32)}-${'1'.repeat(16)}-00`), /invalid/);
  const invalid = run(`new Writer(${JSON.stringify(join(dir, 'bad.jsonl'))}, 'tool','1');`,
    { ...process.env, STEPTRACE_TRACEPARENT: 'bad' });
  assert.notEqual(invalid.status, 0);
  assert.ok(!readdirSync(dir).includes('bad.jsonl'));
});

test('sink failure is nonfatal only for environment writers', t => {
  // Linux /dev/full reliably reproduces a full sink, even when tests run as root.
  const dir = fixture(t);
  const result = run(`
    import { closeSync, openSync } from 'node:fs';
    const w = Writer.fromEnv('tool','1');
    closeSync(w.file); w.file = openSync('/dev/full','w');
    w.point('operation'); w.close(); console.log('product completed');
  `, { ...process.env, STEPTRACE_DIR: dir, STEPTRACE_TRACEPARENT: undefined });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), 'product completed');
  assert.equal(result.stderr.match(/steptrace: tracing disabled/g).length, 1);
});

test('killed writer stays unfinished and exported without synthetic ends', async t => {
  const dir = fixture(t);
  const path = join(dir, 'killed.jsonl');
  const proc = spawn(process.execPath, ['--input-type=module', '-e', `
    import {Writer} from ${JSON.stringify(moduleUrl)};
    const w = new Writer(${JSON.stringify(path)},'oxblend','1');
    w.start('pack'); console.log('ready'); process.stdin.resume();
  `], { cwd: root, stdio: ['pipe', 'pipe', 'pipe'] });
  t.after(() => { if (proc.exitCode === null) proc.kill('SIGKILL'); });
  await once(proc.stdout, 'data');
  const exit = once(proc, 'exit');
  proc.kill('SIGKILL'); await exit;
  assert.equal(rows(path).length, 2);
  assert.equal(check(path).status, 1);
  const out = join(dir, 'export');
  const result = spawnSync('python3', ['-m', 'steptrace', 'export', path, '--out', out], { cwd: root });
  assert.equal(result.status, 1);
  assert.equal(rows(join(out, '1.jsonl')).length, 2);
});

test('asynchronous children can outlive their launching span', t => {
  const path = join(fixture(t), 'async.jsonl');
  const w = new Writer(path, 'agent', '1');
  const launch = w.start('tool.call');
  const child = w.start('agent.subagent', {}, launch);
  w.end(launch, 'ok');
  w.point('late', { notes: 'caller note' }, launch);
  assert.equal(rows(path).at(-1).data.late_span, launch);
  assert.deepEqual(rows(path).at(-1).data.notes, ['caller note', 'point after span end']);
  w.end(child, 'ok');
  w.close();
  const before = readFileSync(path, 'utf8');
  w.point('after-close');
  assert.equal(readFileSync(path, 'utf8'), before);
  assert.equal(check(path).status, 0);
});

test('start clock is captured after the start write', t => {
  class ObservedWriter extends Writer {
    emit(...args) { super.emit(...args); this.lastWrite = performance.now(); }
  }
  const w = new ObservedWriter(join(fixture(t), 'clock.jsonl'), 'test', '1', {
    argv: ['--token', 'fake-token', 'key=fake-key', '---token', 'fake-triple', '--token=']
  });
  assert.ok(w.active.get(w.root).begun >= w.lastWrite);
  const span = w.start('work');
  assert.ok(w.active.get(span).begun >= w.lastWrite);
  w.end(span, 'ok'); w.close();
  const result = rows(w.path);
  assert.deepEqual(result.map(row => row.seq), result.map((_, i) => i));
  assert.ok(!readFileSync(w.path, 'utf8').includes('fake-token'));
  assert.ok(!readFileSync(w.path, 'utf8').includes('fake-key'));
  assert.ok(!readFileSync(w.path, 'utf8').includes('fake-triple'));
  assert.equal(result[0].data.argv.at(-1), '--token=<redacted>');
});

test('invalid JSON payloads and inactive spans are rejected', t => {
  const w = new Writer(join(fixture(t), 'strict.jsonl'), 'tool', '1');
  w.point('numbers', { nan: NaN, inf: Infinity, unknown: null });
  const data = rows(w.path)[1].data;
  assert.equal(data.nan, null);
  assert.equal(data.inf, null);
  assert.ok(data.notes.includes('non-finite number replaced with null'));
  w.point('unknown', { unknown: null });
  assert.equal(rows(w.path)[2].data.notes, undefined);
  assert.throws(() => w.point('bad', { score: undefined }), /JSON values/);
  assert.throws(() => w.point('run'), /reserved/);
  assert.throws(() => w.start('bad', {}, 'missing'), /inactive/);
  w.close();
});
