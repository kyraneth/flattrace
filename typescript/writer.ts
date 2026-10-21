// SPDX-License-Identifier: MIT
// Node's native TypeScript stripping; no runtime or build dependencies.
import { randomBytes } from 'node:crypto';
import { openSync, writeFileSync, closeSync, mkdirSync } from 'node:fs';
import { hostname } from 'node:os';
import { join } from 'node:path';
import { performance } from 'node:perf_hooks';

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Data = { [key: string]: Json };
export type TraceError = { kind: string; message: string; [key: string]: Json };
type Options = { argv?: string[]; data?: Data; directory?: string };
export const CONTEXT_ENV = 'FLATTRACE_TRACEPARENT';
export const LEVEL_ENV = 'FLATTRACE_LEVEL';
function levelFromEnv(): [number, string | null] {
  const raw = (process.env[LEVEL_ENV] ?? '').replace(levelWhitespace, '');
  if (!raw) return [1, null];
  const level = Number(raw);
  // JSON/Number can represent integer levels exactly only in the safe range.
  if (/^[0-9]+$/.test(raw) && Number.isSafeInteger(level)) return [level, null];
  return [1, `invalid ${LEVEL_ENV} '${raw}' treated as 1`];
}
let warned = false;
function warn(error: unknown, disabled = true) {
  if (!warned) {
    warned = true;
    try { console.error(`flattrace: ${disabled ? 'tracing disabled: ' : ''}${error}`); }
    catch { /* stderr failure must not interrupt product work */ }
  }
}
function ioError(error: unknown): boolean {
  return error instanceof Error && 'code' in error;
}
export function context(value: string): [string, string, string] {
  // W3C traceparent widths, not workload limits.
  const m = /^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$/.exec(value);
  if (!m || /^0+$/.test(m[1]) || /^0+$/.test(m[2])) throw new Error('invalid FLATTRACE_TRACEPARENT');
  return [m[1], m[2], m[3]];
}
function id(bytes: number): string {
  const value = randomBytes(bytes).toString('hex');
  return /^0+$/.test(value) ? id(bytes) : value;
}
// Match Python re.I (including dotted/dotless i) and its Unicode word/space sets.
const secretName = '(?:token|password|passwd|secret|ap[iİı][-_]?key|access[-_]?key|key)';
const space = '\\p{White_Space}\\x1c-\\x1f';
const levelWhitespace = new RegExp(`^[${space}]+|[${space}]+$`, 'gu');
const boundary = '(?<![\\p{L}\\p{N}_])';
const flag = new RegExp(`^-*${secretName}$`, 'iu');
const auth = new RegExp(`${boundary}(Author[iİı]zat[iİı]on[${space}]*:[${space}]*)(?:Bearer|Bas[iİı]c)[${space}]+[^${space},;]+`, 'giu');
const urlAuth = new RegExp(`(https?://)[^/${space}:@]+:[^/${space}@]+@`, 'giu');
const assignment = new RegExp(`${boundary}(${secretName}[${space}]*[=:][${space}]*)(?:"[^"]*"|'[^']*'|[^${space},;&]+)`, 'giu');
const flagText = new RegExp(`(-+${secretName}[${space}]+)(?:"[^"]*"|'[^']*'|[^${space},;]+)`, 'giu');
function credentialText(value: string): string {
  return value.replace(auth, '$1<redacted>').replace(urlAuth, '$1<redacted>@')
    .replace(assignment, '$1<redacted>').replace(flagText, '$1<redacted>');
}
export function sanitizeArgv(argv: string[]): string[] {
  let hideNext = false;
  return argv.map(value => {
    if (hideNext) { hideNext = false; return '<redacted>'; }
    const equal = value.indexOf('=');
    if (equal !== -1 && flag.test(value.slice(0, equal))) return value.slice(0, equal + 1) + '<redacted>';
    hideNext = flag.test(value);
    return credentialText(value);
  });
}
type Normalization = { notes: Set<string>; active: Set<object> };
function normalization(): Normalization { return { notes: new Set(), active: new Set() }; }
function representation(value: unknown): string {
  try { return String(value); } catch { return '<unserializable value>'; }
}
function object(value: unknown, state: Normalization): Data {
  const data = numbers(value, state);
  if (data !== null && !Array.isArray(data) && typeof data === 'object') return data;
  state.notes.add('non-object data wrapped in value');
  return { value: data };
}
function numbers(value: unknown, state: Normalization): Json {
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) { state.notes.add('non-finite number replaced with null'); return null; }
    return value;
  }
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value !== 'object') {
    state.notes.add('unserializable value replaced with text');
    return representation(value);
  }
  if (state.active.has(value)) {
    state.notes.add('circular reference replaced with text');
    return '<circular reference>';
  }
  state.active.add(value);
  try {
    const read = (key: string): Json => {
      try { return numbers((value as Record<string, unknown>)[key], state); }
      catch { state.notes.add('unserializable value replaced with text'); return '<unserializable value>'; }
    };
    // Snapshot once; do not invoke caller toJSON hooks. Preserve sparse nulls and __proto__.
    if (Array.isArray(value)) return Array.from({ length: value.length }, (_, i) => i in value ? read(String(i)) : null);
    if (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null) {
      return Object.fromEntries(Object.keys(value).map(key => [key, read(key)]));
    }
    state.notes.add('unserializable value replaced with text');
    return representation(value);
  } catch {
    state.notes.add('unserializable value replaced with text');
    return '<unserializable value>';
  } finally { state.active.delete(value); }
}
function notedObject(value: unknown): Data {
  const state = normalization();
  let data = object(value, state);
  for (const note of [...state.notes].sort()) data = addNote(data, note);
  return data;
}
function addNote(data: Data, note: string): Data {
  const existing = 'notes' in data ? data.notes : [];
  return { ...data, notes: [...(Array.isArray(existing) ? existing : [existing]), note] };
}
function filename(tool: string, run: string): string {
  if (!/^[a-z0-9_-]+$/.test(tool)) throw new Error('environment writer tool must be a lowercase slug');
  // Date supplies milliseconds: do not imply finer precision.
  const stamp = new Date().toISOString().replace(/[-:]/g, '');
  return `${stamp}-${tool}-${run}.jsonl`;
}

export class Writer {
  readonly tool: string;
  readonly tool_version: string;
  readonly trace: string;
  readonly parent: string | null;
  readonly flags: string;
  run: string;
  readonly root: string;
  readonly path: string;
  level = 1; // fromEnv sets the FLATTRACE_LEVEL value.
  private file: number | null;
  private bestEffort: boolean;
  private seq = 0;
  private pid = process.pid;
  private active = new Map<string, { event: string; parent: string | null; begun: number }>();

  static fromEnv(tool: string, version: string, options: Options = {}): Writer | null {
    const directory = process.env.FLATTRACE_DIR;
    const [level, note] = levelFromEnv();
    if (!directory || level === 0) return null;
    let data: Data = { ...notedObject(options.data ?? {}), level };
    if (note !== null) data = addNote(data, note);
    try {
      mkdirSync(directory, { recursive: true });
      const writer = new Writer(null, tool, version, { ...options, data, directory });
      writer.level = level;
      return writer;
    } catch (error) {
      if (!ioError(error)) throw error;
      warn(error);
      return null;
    }
  }

  constructor(path: string | null, tool: string, version: string, options: Options = {}) {
    if (typeof tool !== 'string' || typeof version !== 'string') throw new TypeError('tool and tool_version must be strings');
    const inherited = process.env[CONTEXT_ENV];
    [this.trace, this.parent, this.flags] = inherited === undefined ? [id(16), null, '00'] : context(inherited);
    this.tool = tool;
    this.tool_version = version;
    this.run = id(16);
    this.root = id(8);
    this.bestEffort = options.directory !== undefined;
    while (true) {
      this.path = options.directory === undefined ? path! : join(options.directory, filename(tool, this.run));
      try { this.file = openSync(this.path, 'ax'); break; }
      catch (error) {
        if (!options.directory || (error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
        this.run = id(16);
      }
    }
    this.begin('run', { ...notedObject(options.data ?? {}), argv: sanitizeArgv(options.argv ?? []),
      host: hostname(), version, pid: this.pid }, this.parent, this.root);
  }

  private emit(event: string, state: string, span: string, parent: string | null, data: Data, extra: Data = {}) {
    if (typeof event !== 'string') throw new TypeError('event must be a string');
    if (process.pid !== this.pid) {
      warn('writer inherited across fork; create a new writer');
      if (this.file !== null) closeSync(this.file);
      this.file = null;
      return;
    }
    const normalizationState = normalization();
    data = object(data, normalizationState);
    extra = object(extra, normalizationState);
    for (const note of [...normalizationState.notes].sort()) data = addNote(data, note);
    const row = { v: 1, seq: this.seq++, tool: this.tool, tool_version: this.tool_version,
      run: this.run, trace: this.trace, span, parent, event, state,
      t: new Date().toISOString(), data, ...extra };
    let line: string;
    try { line = JSON.stringify(row); }
    catch {
      row.data = { value: representation(data), notes: ['unserializable value replaced with text'] };
      if ('error' in row) row.error = { kind: 'serialization', message: representation(row.error) };
      line = JSON.stringify(row);
    }
    if (this.file !== null) {
      try { writeFileSync(this.file, line + '\n'); }
      catch (error) {
        if (!this.bestEffort || !ioError(error)) throw error;
        warn(error);
        try { closeSync(this.file); } catch { /* already failed sink */ }
        this.file = null;
      }
    }
  }
  private begin(event: string, data: Data, parent: string | null, span: string): string {
    this.emit(event, 'start', span, parent, data);
    this.active.set(span, { event, parent, begun: performance.now() });
    return span;
  }
  private entry(span: string) {
    const entry = this.active.get(span);
    if (!entry) throw new Error('inactive span');
    return entry;
  }
  start(event: string, data: Data = {}, parent = this.root): string {
    if (event === 'run') throw new Error('run is reserved');
    this.entry(parent);
    return this.begin(event, data, parent, id(8));
  }
  point(event: string, data: Data = {}, span = this.root, error?: TraceError): void {
    if (event === 'run') throw new Error('run is reserved');
    if (!this.active.has(span)) {
      if (!this.active.has(this.root)) return;
      data = addNote({ ...notedObject(data), late_span: span }, 'point after span end');
      span = this.root;
    }
    const entry = this.entry(span);
    this.emit(event, 'point', span, entry.parent, data, this.errorData(error));
  }
  private errorData(error?: TraceError): Data {
    if (error === undefined) return {};
    if (error === null || typeof error !== 'object'
      || typeof error.kind !== 'string' || typeof error.message !== 'string') throw new TypeError('error requires string kind and message');
    if (Object.getPrototypeOf(error) !== Object.prototype && Object.getPrototypeOf(error) !== null) {
      // Keep required error structure even when its caller context needs degradation.
      return { error: { kind: error.kind, message: error.message, context: error } };
    }
    return { error };
  }
  end(span: string, outcome: string, data: Data = {}, error?: TraceError): void {
    if (span === this.root) {
      const message = 'run root can only end through close()';
      if (!this.bestEffort) throw new Error(message);
      warn(message, false);
      return;
    }
    this.endSpan(span, outcome, data, error);
  }
  private endSpan(span: string, outcome: string, data: Data = {}, error?: TraceError): void {
    const entry = this.entry(span);
    if (typeof outcome !== 'string') throw new TypeError('outcome must be a string');
    this.emit(entry.event, 'end', span, entry.parent, data,
      { took_ms: performance.now() - entry.begun, outcome, ...this.errorData(error) });
    this.active.delete(span);
  }
  traceparent(span = this.root): string {
    this.entry(span);
    return `00-${this.trace}-${span}-${this.flags}`;
  }
  childEnv(span = this.root): Record<string, string | undefined> {
    return { ...process.env, [CONTEXT_ENV]: this.traceparent(span) };
  }
  close(outcome = 'ok'): void {
    if ([...this.active.keys()].some(span => span !== this.root)) throw new Error('local spans still active');
    this.endSpan(this.root, outcome);
    if (this.file !== null) {
      try { closeSync(this.file); }
      catch (error) {
        if (!this.bestEffort || !ioError(error)) throw error;
        warn(error);
      }
      this.file = null;
    }
  }
  // No exit handler: killed processes leave unfinished runs, never synthetic success.
}
