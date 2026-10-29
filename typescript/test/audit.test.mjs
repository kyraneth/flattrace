// SPDX-License-Identifier: MIT
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { Writer, sanitizeArgv } from '../writer.ts';

const root = fileURLToPath(new URL('../../', import.meta.url));
function fixture(t) {
  const dir = mkdtempSync(join(tmpdir(), 'flattrace-audit-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return dir;
}
function rows(path) { return readFileSync(path, 'utf8').trim().split('\n').map(JSON.parse); }
function checked(path) {
  const result = spawnSync('python3', ['-m', 'flattrace', 'check', path], { cwd: root, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stdout + result.stderr);
}

test('shared credential vectors apply to argv and the run header', t => {
  const vectors = JSON.parse(readFileSync(new URL('../../tests/fixtures/sanitize-argv.json', import.meta.url)));
  const dir = fixture(t);
  for (const vector of vectors) {
    assert.deepEqual(sanitizeArgv(vector.argv), vector.expected, vector.name);
    const w = new Writer(join(dir, vector.name + '.jsonl'), 'synthetic', '1', { argv: vector.argv });
    w.close();
    assert.deepEqual(rows(w.path)[0].data.argv, vector.expected);
    checked(w.path);
  }
});

test('only close ends the root in strict and harmless modes', t => {
  const dir = fixture(t);
  const w = new Writer(join(dir, 'root.jsonl'), 'synthetic', '1');
  const child = w.start('work');
  assert.throws(() => w.end(w.root, 'ok'), /root.*close/);
  assert.equal(rows(w.path).length, 2);
  w.end(child, 'ok'); w.close(); checked(w.path);
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', `
    import {Writer} from ${JSON.stringify(new URL('../writer.ts', import.meta.url).href)};
    const w = Writer.fromEnv('synthetic', '1');
    const child = w.start('work'); w.end(w.root, 'ok');
    w.end(child, 'ok'); w.close();
  `], { env: { ...process.env, FLATTRACE_DIR: dir, FLATTRACE_LEVEL: '1' }, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  for (const name of readdirSync(dir)) checked(join(dir, name));
});

test('non-ASCII digits fall back and lone surrogates are replaced with a note', t => {
  const dir = fixture(t);
  for (const raw of ['²', '٢', '２', '2²']) {
    const result = spawnSync(process.execPath, ['--input-type=module', '-e', `
      import {Writer} from ${JSON.stringify(new URL('../writer.ts', import.meta.url).href)};
      const w = Writer.fromEnv('synthetic', '1');
      if(w.level !== 1) throw Error('wrong level'); w.close();
    `], { env: { ...process.env, FLATTRACE_DIR: dir, FLATTRACE_LEVEL: raw }, encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
  }
  const w = new Writer(join(dir, 'unicode.jsonl'), 'synthetic', '1');
  w.point('unicode', { high: '\ud800', low: '\udfff', pair: '\ud83d\ude00' });
  w.point('continued'); w.close();
  const data = rows(w.path)[1].data;
  assert.equal(data.high, '\ufffd');
  assert.equal(data.low, '\ufffd');
  assert.equal(data.pair, '\ud83d\ude00');
  assert.ok(data.notes.includes('lone surrogate replaced with U+FFFD'));
  checked(w.path);
});

test('level trimming matches the Python Unicode whitespace set', t => {
  const dir = fixture(t);
  for (const [raw, expected] of [['2\x1c', 2], ['\x1c2', 2], ['2\x85', 2], ['2\ufeff', 1]]) {
    const result = spawnSync(process.execPath, ['--input-type=module', '-e', `
      import {Writer} from ${JSON.stringify(new URL('../writer.ts', import.meta.url).href)};
      const w = Writer.fromEnv('synthetic', '1');
      if(w.level !== ${expected}) throw Error('wrong level'); w.close();
    `], { env: { ...process.env, FLATTRACE_DIR: dir, FLATTRACE_LEVEL: raw }, encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
  }
});

test('envelope string types are API errors, not invalid lines', t => {
  const path = join(fixture(t), 'strings.jsonl');
  assert.throws(() => new Writer(path, 7, '1'), TypeError);
  assert.throws(() => new Writer(path, 'synthetic', 7), TypeError);
  const w = new Writer(path, 'synthetic', '1');
  assert.throws(() => w.start(7), TypeError);
  assert.throws(() => w.point(7), TypeError);
  assert.equal(rows(path).length, 1);
  w.point('continued'); w.close(); checked(path);
});

test('unsupported values, cycles and throwing getters degrade without stopping writes', t => {
  const w = new Writer(join(fixture(t), 'payload.jsonl'), 'synthetic', '1');
  const cycle = { kept: 7 }; cycle.self = cycle;
  const throwing = { get value() { throw Error('synthetic'); }, kept: 7 };
  const payloads = [
    { kept: 7, value: 1n, notes: 'caller' },
    { kept: 7, value: undefined },
    { kept: 7, value: Symbol('synthetic') },
    { kept: 7, value() {} }, cycle, throwing, ['nonobject', 7], new Date(0)
  ];
  for (const payload of payloads) w.point('payload', payload);
  w.point('error-context', {}, w.root, { kind: 'synthetic', message: 'synthetic', value: 1n });
  class ErrorContext { kind = 'synthetic'; message = 'synthetic'; value = 1n; }
  w.point('class-error', {}, w.root, new ErrorContext());
  w.point('continued'); w.close();
  const result = rows(w.path);
  for (const row of result.slice(1, -2)) assert.ok(row.data.notes?.length);
  assert.equal(result[1].data.notes[0], 'caller');
  assert.equal(result[1].data.kept, 7);
  assert.equal(result[6].data.kept, 7);
  assert.equal(result.at(-2).event, 'continued');
  assert.equal(cycle.self, cycle);
  checked(w.path);
});
