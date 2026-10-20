# SPDX-License-Identifier: MIT
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from flattrace import Writer
from flattrace.check import check_events, read_trace

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = {"tool": "my-tool", "events": {
    "run": {"kind": "span", "fields": {}},
    "summary": {"kind": "span", "fields": {}},
    "redraw": {"kind": "point", "fields": {}, "level": 2},
}, "required": ["summary", "redraw"]}


class LevelTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "target").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "target")
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def run_writer(self, level=None, redraw=False):
        env = {"FLATTRACE_DIR": str(self.dir)}
        if level is not None:
            env["FLATTRACE_LEVEL"] = level
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("FLATTRACE_TRACEPARENT", None)
            if level is None:
                os.environ.pop("FLATTRACE_LEVEL", None)
            w = Writer.from_env("my-tool", "1.0")
            if w is None:
                return None
            span = w.start("summary")
            if redraw:
                w.point("redraw", span=span)
            w.end(span, "ok")
            w.close()
            return w

    def rows(self):
        (path,) = self.dir.glob("*.jsonl")
        rows, issues = read_trace(path)
        self.assertEqual(issues, [])
        return rows

    def test_level_zero_writes_nothing(self):
        self.assertIsNone(self.run_writer("0"))
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_default_level_is_one_and_recorded(self):
        w = self.run_writer()
        self.assertEqual(w.level, 1)
        self.assertEqual(self.rows()[0]["data"]["level"], 1)

    def test_invalid_level_is_one_with_note(self):
        self.run_writer("loud")
        data = self.rows()[0]["data"]
        self.assertEqual(data["level"], 1)
        self.assertIn("invalid FLATTRACE_LEVEL", " ".join(data["notes"]))

    def test_child_inherits_level(self):
        code = ("from flattrace import Writer; w = Writer.from_env('child', '1'); "
                "print(w.level); w.close()")
        with patch.dict(os.environ, {"FLATTRACE_DIR": str(self.dir), "FLATTRACE_LEVEL": "2"}):
            os.environ.pop("FLATTRACE_TRACEPARENT", None)
            parent = Writer.from_env("my-tool", "1.0")
            out = subprocess.run([sys.executable, "-c", code], env={**parent.child_env(), "PYTHONPATH": str(ROOT)},
                                 capture_output=True, text=True, check=True).stdout.strip()
            parent.close()
        self.assertEqual(out, "2")

    def test_check_flags_debug_event_in_level_one_run(self):
        self.run_writer("1", redraw=True)
        issues = check_events(self.rows(), CONTRACT)
        self.assertIn("level 2 event redraw in level 1 run", issues)
        self.assertNotIn("missing required event redraw for None", issues)

    def test_check_accepts_debug_event_in_level_two_run(self):
        self.run_writer("2", redraw=True)
        self.assertEqual(check_events(self.rows(), CONTRACT), [])


if __name__ == "__main__":
    unittest.main()
