# SPDX-License-Identifier: MIT
"""Every executable README example is a complete valid v1 record.

README fenced blocks tagged ```json trace:<id> (a whole run file) and
```json events:<id> (a vocabulary file) are extracted byte-exactly and run
against the real checker. CHECKS pairs each run with its vocabularies and
the expected outcome, so a passing example and a deliberately failing one
(collision, hash mismatch) are both verified, not just printed.
"""
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from flattrace.check import check_events, read_events, read_trace

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


def blocks(kind):
    pattern = re.compile(r"```json %s:([A-Za-z0-9_-]+)\n(.*?)\n```" % kind, re.S)
    found = {}
    for name, body in pattern.findall(README.read_text()):
        if name in found:
            raise AssertionError(f"duplicate README {kind} block {name!r}")
        found[name] = body + "\n"
    return found


# (run, [vocabularies], command, expected issue substrings, expected notices)
CHECKS = [
    ("first-run", [], None, [], []),
    ("app-run", ["app-vocab"], None, [],
     ["unverified provenance: run has no data.vocabularies"]),
    ("app-mixed", ["app-vocab", "render-vocab"], None, [], []),
    ("collision-run", ["render-vocab", "render-collide"], None,
     ["conflicting event definition 'render.tile'"], []),
    ("hash-run", ["app-vocab"], None,
     ["not listed in run data.vocabularies", "not supplied"], []),
]


class ReadmeTests(unittest.TestCase):
    def test_displayed_cli_output_matches_real_commands(self):
        examples = re.findall(r"```sh\n([^`]+)\n```\n\n```text\n(.*?)\n```",
                              README.read_text(), re.S)
        self.assertEqual(len(examples), 6)
        aliases = {"mixed": "app-mixed", "collision": "collision-run",
                   "hash": "hash-run", "render-fork": "render-collide"}
        with tempfile.TemporaryDirectory() as directory:
            for name, body in {**blocks("trace"), **blocks("events")}.items():
                suffix = ".jsonl" if name in blocks("trace") else ".json"
                (Path(directory) / (name + suffix)).write_text(body)
            for alias, name in aliases.items():
                suffix = ".jsonl" if name in blocks("trace") else ".json"
                (Path(directory) / (alias + suffix)).write_text(
                    (Path(directory) / (name + suffix)).read_text())
            fixture = Path(directory) / "tests/fixtures/legacy/1.jsonl"
            fixture.parent.mkdir(parents=True)
            fixture.write_bytes((ROOT / "tests/fixtures/legacy/1.jsonl").read_bytes())
            for command, shown in examples:
                args = shlex.split(command)
                self.assertEqual(args.pop(0), "flattrace")
                result = subprocess.run([sys.executable, "-m", "flattrace", *args],
                                        cwd=directory, capture_output=True, text=True,
                                        env={**os.environ, "PYTHONPATH": str(ROOT)})
                with self.subTest(command=command):
                    self.assertEqual(result.returncode,
                                     1 if args[1] in ("collision.jsonl", "hash.jsonl") else 0,
                                     result.stderr)
                    self.assertEqual(result.stdout.rstrip(), shown)

    def test_every_tagged_block_is_executed(self):
        traces = blocks("trace")
        vocabs = blocks("events")
        used_runs = {run for run, _, _, _, _ in CHECKS}
        used_vocabs = {vocab for _, names, _, _, _ in CHECKS for vocab in names}
        self.assertEqual(set(traces), used_runs)
        self.assertEqual(set(vocabs), used_vocabs)

    def test_examples_check_as_documented(self):
        traces = blocks("trace")
        vocabs = blocks("events")
        with tempfile.TemporaryDirectory() as directory:
            files = {}
            for name, body in {**traces, **vocabs}.items():
                path = Path(directory) / f"{name}.json"
                path.write_text(body)
                files[name] = path
            for run, names, command, problems, notices in CHECKS:
                contracts = [read_events(files[name]) for name in names]
                rows, file_issues, file_notices = read_trace(files[run])
                issues = list(file_issues)
                reported = list(file_notices)
                if contracts:
                    extra, extra_notices = check_events(rows, contracts, command)
                    issues.extend(extra)
                    reported.extend(extra_notices)
                for want in problems:
                    self.assertTrue(any(want in issue for issue in issues),
                                    f"{run}: expected {want!r} in {issues}")
                if not problems:
                    self.assertEqual(issues, [], run)
                self.assertEqual(reported, notices, run)


if __name__ == "__main__":
    unittest.main()
