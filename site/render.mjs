// SPDX-License-Identifier: MIT
// Render site/out/index.md (derived from README.md by build.py) to a static
// page in site/dist. Mermaid blocks become inline SVG drawn by Pele at build
// time, themed by CSS variables; on phones a wide diagram scrolls sideways at
// a readable size. The page ships no JavaScript.
import { readFileSync, writeFileSync, mkdirSync, copyFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Marked } from 'marked';
import { render } from 'pele';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const dist = join(here, 'dist');
const repo = 'https://github.com/kyraneth/flattrace';

const md = readFileSync(join(here, 'out', 'index.md'), 'utf8')
  .replace(/^# flattrace\n\n/, '')
  // Relative README links point into the repository.
  .replace(/\]\((?!https?:|#)([^)]+)\)/g, `](${repo}/blob/main/$1)`);

const marked = new Marked({
  renderer: {
    code({ text, lang }) {
      if (lang === 'mermaid') return `<figure class="diagram"><div class="diagram-scroll" tabindex="0" role="region" aria-label="Scrollable diagram">${render(text).svg}</div><figcaption>Scroll sideways to see the whole diagram.</figcaption></figure>\n`;
      return false;
    },
  },
});
const body = marked.parse(md);

const css = `
:root{--bg:#F1EDE1;--fg:#29312D;--muted:#5d665f;--accent:#AA4528;--code:#e6e1d2;--border:#cfc8b6;
--pele-bg:transparent;--pele-fg:var(--fg);--pele-muted:var(--muted);--pele-line:var(--fg);
--pele-surface:#e9e4d6;--pele-surface-alt:#ddd7c6;--pele-border:var(--border)}
@media (prefers-color-scheme:dark){:root{--bg:#232a26;--fg:#F1EDE1;--muted:#b5b1a5;--accent:#F2AA7E;
--code:#2d3530;--border:#46504a;--pele-surface:#2d3530;--pele-surface-alt:#36403a}}
*{box-sizing:border-box}html{background:var(--bg);color:var(--fg)}
body{margin:0 auto;max-width:46rem;padding:2.5rem 1.25rem 4rem;font:17px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}
header{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin-bottom:2rem}
header img{height:44px}.light{display:block}.dark{display:none}
@media (prefers-color-scheme:dark){.light{display:none}.dark{display:block}}
nav a{margin-left:1rem}a{color:var(--accent)}h2{margin-top:2.6rem;border-bottom:1px solid var(--border);padding-bottom:.3rem}
code{font:0.88em/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--code);padding:.1em .3em;border-radius:4px}
pre{background:var(--code);padding:1rem;border-radius:8px;overflow-x:auto}pre code{padding:0;background:none}
table{border-collapse:collapse;width:100%;font-size:.95em}th,td{border:1px solid var(--border);padding:.45rem .6rem;text-align:left;vertical-align:top}
.diagram{margin:1.5rem 0;text-align:center}.diagram-scroll{overflow-x:auto}.diagram svg{max-width:none!important;height:auto}
.diagram figcaption{display:none;font-size:.8em;color:var(--muted)}
@media (max-width:600px){.diagram{text-align:left}.diagram figcaption{display:block}}
.tagline{font-size:1.1em}footer{margin-top:3rem;color:var(--muted);font-size:.9em}`;

const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>flattrace — flat file, deep trace</title>
<meta name="description" content="flattrace records what a tool did as small, local JSONL events, checked against vocabularies each tool owns.">
<link rel="icon" type="image/png" href="flattrace-mark-light-32.png">
<style>${css}</style></head><body>
<header><a href="/"><img class="light" src="flattrace-wordmark-light.svg" alt="flattrace"><img class="dark" src="flattrace-wordmark-dark.svg" alt="flattrace"></a>
<nav><a href="${repo}">GitHub</a><a href="${repo}/blob/main/SPEC.md">Spec</a></nav></header>
<main class="tagline-wrap">${body}</main>
<footer>MIT licensed · <a href="${repo}">source</a> · install: <code>pip install flattrace</code>, <code>cargo add flattrace</code>, <code>npm install flattrace</code></footer>
</body></html>
`;

mkdirSync(dist, { recursive: true });
writeFileSync(join(dist, 'index.html'), html);
for (const f of ['flattrace-wordmark-light.svg', 'flattrace-wordmark-dark.svg', 'flattrace-mark-light-32.png'])
  copyFileSync(join(root, 'assets', 'logo', f), join(dist, f));
console.log(join(dist, 'index.html'));
