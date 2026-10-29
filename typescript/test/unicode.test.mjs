// SPDX-License-Identifier: MIT
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Writer } from '../writer.ts';

test('Unicode policy on writer entry paths', () => {
  const dir = mkdtempSync(join(tmpdir(), 'unicode-'));
  try {
    const path = join(dir, 'trace.jsonl');
    const w = new Writer(path, 'tool', '1');
    w.point('pair\ud83d\ude00', { text: '\ud83d\ude00' });
    w.point('astral😀', { text: '😀' });
    w.point('data', { text: '\ud800' });
    const span = w.start('envelope\ud800');
    w.end(span, 'ok');
    w.point('keys', { '\ud800': 'first', '�': 'second' });
    class SurrogateText { toString() { return '\ud800'; } }
    w.point('fallback', { text: new SurrogateText() });
    w.close();
    const rows = readFileSync(path, 'utf8').trim().split('\n').map(JSON.parse);
    for (const row of rows.slice(1, 3)) {
      assert.deepEqual(row.data, { text: '😀' });
      assert.ok(row.event.endsWith('😀'));
    }
    const note = 'lone surrogate replaced with U+FFFD';
    assert.equal(rows[3].data.text, '�');
    assert.ok(rows[3].data.notes.includes(note));
    for (const row of rows.slice(4, 6)) {
      assert.equal(row.event, 'envelope�');
      assert.ok(row.data.notes.includes(note));
    }
    assert.equal(rows[7].data.text, '�');
    assert.ok(rows[7].data.notes.includes(note));
    assert.ok(rows[7].data.notes.includes('unserializable value replaced with text'));
    assert.equal(rows[6].data['�'], 'first');
    assert.ok(rows[6].data.notes.includes('object keys collide after surrogate replacement'));
    const envelopePath = join(dir, 'tool.jsonl');
    const tool = new Writer(envelopePath, 'tool\ud800', 'version\ud83d\ude00');
    tool.close();
    const first = JSON.parse(readFileSync(envelopePath, 'utf8').split('\n')[0]);
    assert.equal(first.tool, 'tool�');
    assert.equal(first.tool_version, 'version😀');
    assert.ok(first.data.notes.includes(note));
  } finally { rmSync(dir, { recursive: true }); }
});
