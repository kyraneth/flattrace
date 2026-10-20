# steptrace for TypeScript

Dependency-free synchronous JSONL writer for Node >=22.18 with native type
stripping. This is source preparation; the npm package is not published.

```ts
import { Writer } from './writer.ts';
const w = Writer.fromEnv('example', '0.1.0', { argv: ['build'] });
if (w) {
  const span = w.start('build');
  w.end(span, 'ok');
  w.close();
}
```

Set `STEPTRACE_DIR` to enable recording; unset/empty means off. Pass
`w.childEnv(span)` to child processes. Never put credentials into trace payloads;
export and review before sharing. See the [repository](https://github.com/kyraneth/steptrace)
for the format, checker, export command and real Oxblend examples. MIT.
