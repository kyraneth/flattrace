#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build the website's main page text from README.md, the single source.

The page is the README's explanation arc ("One trace line" through "What it
enables"), so the site and the README can never disagree. Writes
site/out/index.md (ignored by git). Hosting and deployment are separate.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIRST, LAST = "## What one trace line is", "## Glossary"


def page(readme: str) -> str:
    start, end = readme.index(FIRST), readme.index(LAST)
    body = readme[start:end].rstrip() + "\n"
    # README test tags and the note about them are for the checker, not for
    # readers of the site.
    body = re.sub(r"Every `json trace:` block.*?\n\n", "", body, count=1, flags=re.S)
    body = re.sub(r"```json (?:trace|events):[A-Za-z0-9_-]+\n", "```json\n", body)
    tagline = re.search(r"^\*\*Flat file, deep trace\.\*\*.*$", readme, re.M).group(0)
    return f"# flattrace\n\n{tagline}\n\n{body}"


def main() -> int:
    out = ROOT / "site" / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.md").write_text(page((ROOT / "README.md").read_text()))
    print(out / "index.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
