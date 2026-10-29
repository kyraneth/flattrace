# SPDX-License-Identifier: MIT
"""Exercise the reachable workflow shell guard without publishing anything."""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseGuardTests(unittest.TestCase):
    def test_npm_source_guard(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text()
        job = workflow.split("\n  publish-npm:\n", 1)[1].split("\n  publish-cli:", 1)[0]
        step = job.split("- name: Check npm source and provenance commits\n", 1)[1].split("\n      - name:", 1)[0]
        self.assertIn("WORKFLOW_SHA: ${{ github.sha }}", step)
        script = re.sub(r"^          ", "", step.split("run: |\n", 1)[1], flags=re.M)
        self.assertNotIn("checkout@", job)
        self.assertNotIn("npm run build", job)
        self.assertIn("NPM_CONFIG_IGNORE_SCRIPTS: 'true'", job)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dist").mkdir()
            (root / "dist/source-commit.txt").write_text("a" * 40 + "\n")
            for ref, workflow_sha, expected in [("a", "a", 0), ("b", "a", 1), ("a", "b", 1)]:
                (root / "release-commit.txt").write_text(ref * 40 + "\n")
                result = subprocess.run(["bash", "-e", "-c", script], cwd=root,
                                        env={**os.environ, "RUNNER_TEMP": directory,
                                             "WORKFLOW_SHA": workflow_sha * 40},
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stderr)
