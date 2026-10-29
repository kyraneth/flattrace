// SPDX-License-Identifier: MIT
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { bindingFile, EngineUnavailable } from '../reader.ts';
import * as reader from '../reader.ts';

const root = fileURLToPath(new URL('../../', import.meta.url));
const corpus = join(root, 'tests', 'conformance', 'corpus');
const goldenDir = join(root, 'tests', 'conformance', 'golden');
const agentEvents = join(root, 'tests', 'conformance', 'agent-events.json');

function corpusFiles() {
  return readdirSync(corpus)
    .filter((name) => name.endsWith('.jsonl') || name.endsWith('.jsonl.gz'))
    .sort()
    .map((name) => join(corpus, name));
}

function golden(name) {
  return JSON.parse(readFileSync(join(goldenDir, name), 'utf8'));
}

function rel(value) {
  if (Array.isArray(value)) return value.map(rel);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => {
        if (k === 'file' && typeof v === 'string' && v.startsWith(corpus + '/')) {
          v = v.slice((corpus + '/').length);
        }
        return [k, rel(v)];
      })
    );
  }
  return value;
}

function approx(actual, expected, path = '$') {
  if (typeof actual === 'number' && typeof expected === 'number') {
    const tol = 1e-9 * Math.max(Math.abs(actual), Math.abs(expected), 1);
    assert.ok(Math.abs(actual - expected) <= tol, `${path}: ${actual} != ${expected}`);
  } else if (Array.isArray(actual) && Array.isArray(expected)) {
    assert.equal(actual.length, expected.length, `${path}: len`);
    actual.forEach((v, i) => approx(v, expected[i], `${path}[${i}]`));
  } else if (actual && expected && typeof actual === 'object' && typeof expected === 'object') {
    assert.deepEqual(new Set(Object.keys(actual)), new Set(Object.keys(expected)), `${path}: keys`);
    for (const k of Object.keys(actual)) approx(actual[k], expected[k], `${path}.${k}`);
  } else {
    assert.deepEqual(actual, expected, path);
  }
}

const EXACT_MESSAGES = [
  'missing envelope fields', 'seq must be a nonnegative integer', 'v must be 1',
  'must be a string', 'invalid trace', 'invalid span', 'invalid parent',
  'run is reserved for lifetime spans', 'invalid state or data', 't must be RFC3339 UTC',
  'end requires took_ms and outcome', 'invalid took_ms', 'took_ms and outcome belong on ends',
  'error requires string kind and message', 'missing run start and end',
  'first line must be run start', 'first valid line must be run start',
  'run start requires argv, host, version', 'file contains different runs or tools',
  'missing run end', 'missing final matching run end', 'last line must be the matching run end',
  'expected one run start', 'seq gap or reordering', 'duplicate span start',
  'unexpected run start', 'inactive parent', 'unmatched end', 'point outside active span',
  'unfinished span', 'unfinished spans',
];

function exactMessage(detail) {
  return EXACT_MESSAGES.some((m) => detail.includes(m));
}

function normalizeIssues(value) {
  if (Array.isArray(value)) return value.map(normalizeIssues);
  if (value && typeof value === 'object') {
    if (Array.isArray(value.issues) && 'rows' in value) {
      value = {
        ...value,
        issues: value.issues.map((issue) => {
          if (typeof issue !== 'string') return issue;
          const cut = issue.indexOf(': ');
          if (
            cut !== -1 &&
            (issue.startsWith('line ') || issue.startsWith('incomplete tail ')) &&
            !exactMessage(issue.slice(cut + 2))
          ) {
            return issue.slice(0, cut + 2);
          }
          return issue;
        }),
      };
    }
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, normalizeIssues(v)]));
  }
  return value;
}

function checkGolden(name, actual) {
  approx(normalizeIssues(rel(actual)), normalizeIssues(golden(name)), name);
}

test('binding file mapping and EngineUnavailable shape', () => {
  assert.equal(bindingFile('linux', 'x64'), 'flattrace-reader.linux-x64-gnu.node');
  assert.equal(bindingFile('darwin', 'arm64'), 'flattrace-reader.darwin-arm64.node');
  assert.equal(bindingFile('sunos', 'x64'), null);
  const error = new EngineUnavailable('sunos', 'x64');
  assert.equal(error.name, 'EngineUnavailable');
  assert.match(error.message, /sunos-x64/);
  assert.ok(error instanceof Error);
});

test('check matches golden', () => {
  checkGolden('check.json', reader.check(corpusFiles()));
});

test('check --events matches golden', () => {
  checkGolden('check_events.json', reader.check([join(corpus, 'c01-parent.jsonl')], [agentEvents]));
});

test('check composition matches golden', () => {
  const comp = join(root, 'tests', 'conformance', 'composition');
  const cases = JSON.parse(readFileSync(join(comp, 'cases.json'), 'utf8')).cases;
  const actual = cases.map((entry) => {
    // String concat, not join: a case may name a vocabulary outside the
    // composition directory (a relative .. path), and join would resolve
    // it while the Python and Rust harnesses keep it verbatim.
    const events = entry.events.map((name) => comp + '/' + name);
    const [result] = reader.check([join(comp, entry.run)], events, entry.command);
    const strip = (text) => text.split(comp + '/').join('');
    return {
      ...result,
      file: entry.run,
      events: [...entry.events],
      issues: result.issues.map(strip),
      notices: result.notices.map(strip),
    };
  });
  checkGolden('check_composition.json', actual);
});

test('spans match goldens', () => {
  const files = corpusFiles();
  checkGolden('spans_all.json', reader.spans(files));
  for (const [name, filter] of [
    ['spans_filtered.json', { event: 'tool.exec', data: { shot: 'sh001' } }],
    ['spans_agent.json', { agent: 'synthetic-agent' }],
    ['spans_query.json', { model: 'synthetic-model-b', outcome: 'ok' }],
    ['spans_session.json', { session: 'session-7' }],
    ['spans_errors.json', { error_kind: 'io' }],
    ['spans_window.json', { t_from: '2026-10-20T00:00:01.000Z', t_to: '2026-10-20T00:00:01.050Z' }],
  ]) {
    checkGolden(name, reader.spans(files, filter));
  }
});

test('digest matches golden with float policy', () => {
  const digest = reader.digest(corpusFiles());
  const turn = digest.table.find((t) => t.tool === 'agent' && t.event === 'agent.turn');
  assert.deepEqual(turn.took, { n: 1, p50: 32, p95: 32, max: 32, total: 32 });
  assert.deepEqual(turn.self, { n: 1, p50: 2.0, p95: 2.0, max: 2.0, total: 2.0 });
  checkGolden('digest.json', digest);
});

test('group matches goldens', () => {
  const files = corpusFiles();
  checkGolden('group.json', reader.group(files, ['tool', 'event']));
  checkGolden('group_label.json', reader.group(files, ['event', 'data.shot']));
});

test('tree matches golden', () => {
  checkGolden('tree.json', reader.tree(corpusFiles(), 'a'.repeat(32)));
});

test('compare matches golden', () => {
  checkGolden(
    'compare.json',
    reader.compare([join(corpus, 'c08-baseline.jsonl')], [join(corpus, 'c09-candidate.jsonl')])
  );
});

test('overlap matches golden', () => {
  checkGolden('overlap.json', reader.overlap(corpusFiles(), 'loop.stall', { event: 'tool.exec' }));
});

test('spans run-end data matches golden', () => {
  const rows = reader.spans([join(corpus, 'closedata', '01.jsonl')]);
  const run = rows.find((r) => r.event === 'run');
  assert.deepEqual(run.end_data, { turns: 3, tokens: 42, totals: { took_ms: 40 } });
  checkGolden('spans_runend.json', rows);
});

test('parity corpus digest and model filters match goldens', () => {
  const files = readdirSync(join(corpus, 'parity'))
    .sort()
    .map((name) => join(corpus, 'parity', name));
  checkGolden('parity_digest.json', reader.digest(files));
  checkGolden('parity_spans_model_nested.json', reader.spans(files, { model: 'nested-m' }));
  assert.deepEqual(reader.spans(files, { model: 'nested-m' }), []);
  const matched = reader.spans(files, { model: 'env-m' });
  checkGolden('parity_spans_model_empty.json', matched);
  assert.deepEqual(
    matched.map((r) => [r.event, r.span]),
    [['run', '1111111111111111'], ['work', '2222222222222222']]
  );
});
