# SPDX-License-Identifier: MIT
"""Guard the intended build and publication boundaries."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseGuardTests(unittest.TestCase):
    def test_only_configured_github_environments_and_no_registry_token_publishes(self):
        workflow = (ROOT / '.github/workflows/release.yml').read_text()
        import re
        self.assertEqual(set(re.findall(r'^    environment: (\S+)$', workflow, re.M)), {'pypi', 'cli'})
        self.assertNotRegex(workflow, r'(?m)^  publish-(npm|crates):')
        self.assertNotRegex(workflow, r'(?m)^\s+- (npm|crates)$')
        self.assertNotIn('cargo +1.94.1 publish', workflow)
        self.assertNotIn('npm publish', workflow)
        self.assertIn('uv publish --trusted-publishing always', workflow)
        self.assertIn('cargo +1.94.1 package --locked', workflow)
        self.assertIn('cli/* crates/*.crate | tee SHA256SUMS', workflow)
