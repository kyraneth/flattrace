# SPDX-License-Identifier: MIT
"""The site page is derived from the README's explanation arc, never a copy."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("site_build", ROOT / "site" / "build.py")
site_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_build)


class SiteTests(unittest.TestCase):
    def test_page_is_the_readme_arc(self):
        readme = (ROOT / "README.md").read_text()
        page = site_build.page(readme)
        for heading in ("## What one trace line is", "## The vocabulary: who says what an event means",
                        "## Several owners, one run", "## Why not the alternatives",
                        "## How this serves reading and learning", "```mermaid"):
            self.assertIn(heading, page)
        self.assertNotIn("## Install", page)
        self.assertNotIn("json trace:", page)


if __name__ == "__main__":
    unittest.main()
