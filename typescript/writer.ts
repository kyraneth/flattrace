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
let warned = false;
function warn(error: unknown) {
  if (!warned) {
    console.error(`flattrace: tracing disabled: ${error}`);
    warned = true;
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
function validateJson(value: unknown): void {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
  if (typeof value === 'number') return; // numbers() handles non-finite values.
  if (Array.isArray(value)) { value.forEach(validateJson); return; }
  if (typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype) {
    Object.values(value).forEach(validateJson); return;
  }
  throw new TypeError('data must contain JSON values');
}
function object(data: unknown): asserts data is Data {
  if (data === null || Array.isArray(data) || typeof data !== 'object') throw new TypeError('data must be an object');
  validateJson(data);
}
export function sanitizeArgv(argv: string[]): string[] {
  let hideNext = false;
  return argv.map(value => {
    if (hideNext) { hideNext = false; return '<redacted>'; }
    const match = /^-*(token|password|passwd|secret|api[-_]?key|access[-_]?key|key)(=|$)/i.exec(value);
    if (!match) return value;
    if (match[2] === '=') return value.slice(0, match[0].length) + '<redacted>';
    hideNext = true;
    return value;
  });
}
function numbers(value: Json, changed: { value: boolean }): Json {
  if (typeof value === 'number' && !Number.isFinite(value)) { changed.value = true; return null; }
  if (Array.isArray(value)) return value.map(item => numbers(item, changed));
  if (value !== null && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, numbers(item, changed)]));
  return value;
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
  private file: number | null;
  private bestEffort: boolean;
  private seq = 0;
  private pid = process.pid;
  private active = new Map<string, { event: string; parent: string | null; begun: number }>();

  static fromEnv(tool: string, version: string, options: Options = {}): Writer | null {
    const directory = process.env.FLATTRACE_DIR;
    if (!directory) return null;
    try {
      mkdirSync(directory, { recursive: true });
      return new Writer(null, tool, version, { ...options, directory });
    } catch (error) {
      if (!ioError(error)) throw error;
      warn(error);
      return null;
    }
  }

  constructor(path: string | null, tool: string, version: string, options: Options = {}) {
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
    this.begin('run', { ...options.data, argv: sanitizeArgv(options.argv ?? []),
      host: hostname(), version, pid: this.pid }, this.parent, this.root);
  }

  private emit(event: string, state: string, span: string, parent: string | null, data: Data, extra: Data = {}) {
    if (process.pid !== this.pid) {
      warn('writer inherited across fork; create a new writer');
      if (this.file !== null) closeSync(this.file);
      this.file = null;
      return;
    }
    object(data);
    object(extra);
    const changed = { value: false };
    data = numbers(data, changed) as Data;
    extra = numbers(extra, changed) as Data;
    if (changed.value) data = addNote(data, 'non-finite number replaced with null');
    object(data);
    const line = JSON.stringify({ v: 1, seq: this.seq++, tool: this.tool, tool_version: this.tool_version,
      run: this.run, trace: this.trace, span, parent, event, state,
      t: new Date().toISOString(), data, ...extra });
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
      data = addNote({ ...data, late_span: span }, 'point after span end');
      span = this.root;
    }
    const entry = this.entry(span);
    this.emit(event, 'point', span, entry.parent, data, this.errorData(error));
  }
  private errorData(error?: TraceError): Data {
    if (error === undefined) return {};
    if (error === null || typeof error !== 'object'
      || typeof error.kind !== 'string' || typeof error.message !== 'string') throw new TypeError('error requires string kind and message');
    return { error };
  }
  end(span: string, outcome: string, data: Data = {}, error?: TraceError): void {
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
  childEnv(span = this.root): NodeJS.ProcessEnv {
    return { ...process.env, [CONTEXT_ENV]: this.traceparent(span) };
  }
  close(outcome = 'ok'): void {
    if ([...this.active.keys()].some(span => span !== this.root)) throw new Error('local spans still active');
    this.end(this.root, outcome);
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
