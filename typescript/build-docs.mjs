// Package docs are generated from the repository's shared README and license.
import { copyFileSync } from 'node:fs';
for (const name of ['README.md', 'LICENSE']) {
  copyFileSync(new URL('../' + name, import.meta.url), new URL(name, import.meta.url));
}
