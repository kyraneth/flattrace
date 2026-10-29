// Package docs and the shared conventions vocabulary are generated from
// the repository's shared files.
import { copyFileSync, mkdirSync } from 'node:fs';
for (const name of ['README.md', 'LICENSE']) {
  copyFileSync(new URL('../' + name, import.meta.url), new URL(name, import.meta.url));
}
mkdirSync(new URL('schema/', import.meta.url), { recursive: true });
copyFileSync(
  new URL('../schema/conventions-events.json', import.meta.url),
  new URL('schema/conventions-events.json', import.meta.url),
);
