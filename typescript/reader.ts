// SPDX-License-Identifier: MIT
// Optional compiled reader engine for flattrace v1 (`flattrace/reader`).
//
// The default export of this package stays the pure writer. This module loads
// a bundled native binary for linux-x64-gnu or darwin-arm64 and otherwise
// throws EngineUnavailable naming the platform. No postinstall downloads,
// no optionalDependencies: both .node files ship inside the one package and
// the darwin-arm64 binary is built at release time (see bench/READER.md and
// the release workflow). Importing this module does not require the engine,
// but calling any function does.
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export class EngineUnavailable extends Error {
  readonly code = 'EngineUnavailable';
  constructor(platform: string, arch: string, detail?: string) {
    super(
      `flattrace reader engine unavailable: no bundled binary for ${platform}-${arch}` +
        (detail ? ` (${detail})` : '') +
        `; only linux-x64-gnu and darwin-arm64 ship with this package`
    );
    this.name = 'EngineUnavailable';
  }
}

type NativeBinding = {
  check(files: string[], events?: string[], command?: string): string;
  stats(files: string[]): string;
  spans(files: string[], filter?: string): string;
  digest(files: string[], scope?: string): string;
  group(files: string[], by: string[], metrics?: string, filter?: string): string;
  tree(files: string[], trace: string): string;
  compare(aFiles: string[], bFiles: string[], filter?: string): string;
  overlap(files: string[], window: string, filter?: string): string;
};

const here = dirname(fileURLToPath(import.meta.url));

export function bindingFile(platform: string, arch: string): string | null {
  if (platform === 'linux' && arch === 'x64') {
    return 'flattrace-reader.linux-x64-gnu.node';
  }
  if (platform === 'darwin' && arch === 'arm64') {
    return 'flattrace-reader.darwin-arm64.node';
  }
  return null;
}

function candidateDirs(): string[] {
  // reader.ts runs from the package root, compiled dist/reader.js one level down.
  return [join(here, 'native'), join(here, '..', 'native')];
}

let cached: NativeBinding | null = null;

function engine(): NativeBinding {
  if (!cached) {
    const file = bindingFile(process.platform, process.arch);
    if (!file) throw new EngineUnavailable(process.platform, process.arch);
    const require = createRequire(import.meta.url);
    let loaded: NativeBinding | null = null;
    let lastError: unknown = null;
    for (const dir of candidateDirs()) {
      try {
        loaded = require(join(dir, file)) as NativeBinding;
        break;
      } catch (error) {
        lastError = error;
      }
    }
    if (!loaded) {
      throw new EngineUnavailable(
        process.platform,
        process.arch,
        lastError instanceof Error ? lastError.message : String(lastError)
      );
    }
    cached = loaded;
  }
  return cached;
}

function dump(value: unknown): string | undefined {
  return value === undefined || value === null ? undefined : JSON.stringify(value);
}

export function check(files: string[], events?: string[], command?: string): unknown {
  return JSON.parse(engine().check(files, events, command));
}

export function stats(files: string[]): unknown {
  return JSON.parse(engine().stats(files));
}

export function spans(files: string[], filter?: Record<string, unknown>): unknown {
  return JSON.parse(engine().spans(files, dump(filter)));
}

export function digest(files: string[], scope?: Record<string, unknown>): unknown {
  return JSON.parse(engine().digest(files, dump(scope)));
}

export function group(
  files: string[],
  by: string[],
  metrics?: string[],
  filter?: Record<string, unknown>
): unknown {
  return JSON.parse(engine().group(files, by, dump(metrics), dump(filter)));
}

export function tree(files: string[], trace: string): unknown {
  return JSON.parse(engine().tree(files, trace));
}

export function compare(aFiles: string[], bFiles: string[], filter?: Record<string, unknown>): unknown {
  return JSON.parse(engine().compare(aFiles, bFiles, dump(filter)));
}

export function overlap(files: string[], window: string, filter?: Record<string, unknown>): unknown {
  return JSON.parse(engine().overlap(files, window, dump(filter)));
}
