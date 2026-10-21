# SPDX-License-Identifier: MIT
"""Keep the extracted conventions linked and v1 clarifications compatible."""
import json
import re
from pathlib import Path
import tempfile
import unittest

from flattrace import Writer
from flattrace.check import check_events, read_events, read_trace

ROOT = Path(__file__).resolve().parents[1]


class SpecDocumentationTests(unittest.TestCase):
    def test_learning_is_linked_outside_core_spec(self):
        spec = (ROOT / "SPEC.md").read_text()
        learning = (ROOT / "docs/learning.md").read_text()
        self.assertIn("docs/learning.md", spec)
        self.assertNotIn("## Learning conventions (all in data)", spec)
        for concept in ("comparability block", "task_hash", "config_hash", "retry_of",
                        "verdict", "reward", "grader", "terminated vs truncated"):
            self.assertIn(concept, learning)
        self.assertIn("MUST", spec)
        self.assertIn("SHOULD", spec)
        # Markdown links in the extracted doc must resolve after the text move.
        for target in re.findall(r"\]\(([^)]+)\)", learning):
            if "://" not in target and not target.startswith("#"):
                self.assertTrue((ROOT / "docs" / target.split("#")[0]).exists(), target)

    def test_skill_event_contract_works_with_emitted_trace(self):
        reference = (ROOT / "skills/flattrace/references/instrumentation.md").read_text()
        sample = re.search(r"```json\n(.*?)\n```", reference, re.S).group(1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            contract_path = path / "flattrace-events.json"
            contract_path.write_text(json.dumps(json.loads(sample)))
            contract = read_events(contract_path)
            writer = Writer(path / "trace.jsonl", "demo", "0.1.0",
                            data={"command": "calculate.ok", "level": 1})
            span = writer.start("calculate", {"items": 3})
            writer.point("result", {"value": 6}, span=span)
            writer.end(span, "ok")
            writer.close()
            rows, issues = read_trace(path / "trace.jsonl")
            self.assertEqual(issues, [])
            self.assertEqual(check_events(rows, contract), [])

    def test_utc_spellings_keep_old_files_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.jsonl"
            writer = Writer(path, "synthetic", "1")
            writer.close()
            original = path.read_text()
            for suffix in ("Z", "+00:00"):
                path.write_text(re.sub(r'Z"', suffix + '"', original))
                self.assertEqual(read_trace(path)[1], [])

    def test_oxblend_documentation_is_version_neutral(self):
        for name in ("SPEC.md", "ROADMAP.md", "examples/oxblend/flattrace-events.json"):
            text = (ROOT / name).read_text()
            self.assertNotRegex(text, r"Oxblend 1\.3\.0")
