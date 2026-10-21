# flattrace logo

The logo artwork is MIT licensed under the repository's [LICENSE](../../LICENSE).

- `flattrace-mark-{light,dark}.svg`: standalone mark, variant 17.
- `flattrace-wordmark-{light,dark}.svg`: wordmark lockup, variant 17a.
- `flattrace-mark-{light,dark}-{16,32,128,512}.png`: icons for
  favicon/avatar consumers that require raster images; prefer the SVGs otherwise.

`light` and `dark` identify the intended background theme. The chosen SVGs and
PNG renders are preserved unchanged. The wordmark's mark serves as the “f”;
the two “t” stems share one neutral crossbar.

Artwork is repository documentation, not a runtime dependency. Cargo's `include`
list, Python's `MANIFEST.in`/explicit package selection, and npm's `files` list
keep it out of the language packages.
