# SPDX-License-Identifier: MIT
"""External-audit regressions through the writer and common checker."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flattrace import Writer, sanitize_argv
from flattrace.check import read_trace
from flattrace.writer import level_from_env

ROOT = Path(__file__).resolve().parents[1]


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic.jsonl"
        self.env = patch.dict(os.environ)
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop("FLATTRACE_TRACEPARENT", None)
        os.environ.pop("FLATTRACE_LEVEL", None)

    def checked(self):
        rows, issues, _ = read_trace(self.path)
        self.assertEqual(issues, [])
        return rows

    def test_level_accepts_only_ascii_digits(self):
        for raw in ("²", "٢", "２", "2²"):
            with self.subTest(raw=raw), patch.dict(os.environ, FLATTRACE_LEVEL=raw):
                level, note = level_from_env()
                self.assertEqual(level, 1)
                self.assertIn("treated as 1", note)

    def test_level_whitespace_is_the_python_unicode_set(self):
        for raw in ("2\x1c", "\x1c2", "2\x85"):
            with self.subTest(raw=raw), patch.dict(os.environ, FLATTRACE_LEVEL=raw):
                self.assertEqual(level_from_env(), (2, None))
        with patch.dict(os.environ, FLATTRACE_LEVEL="2\ufeff"):
            level, note = level_from_env()
            self.assertEqual(level, 1)
            self.assertIsNotNone(note)

    def test_envelope_string_types_are_api_errors_not_invalid_lines(self):
        for field in ("tool", "tool_version"):
            values = {"tool": "synthetic", "tool_version": "1"}
            values[field] = 7
            with self.subTest(field=field), self.assertRaises(TypeError):
                Writer(self.path, **values)
            self.assertFalse(self.path.exists())
        writer = Writer(self.path, "synthetic", "1")
        for call in (writer.start, writer.point):
            with self.assertRaises(TypeError):
                call(7)
        self.assertEqual(len(self.path.read_bytes().splitlines()), 1)
        writer.point("continued")
        writer.close()
        self.checked()

    def test_all_surrogates_are_safe_and_writing_continues(self):
        writer = Writer(self.path, "synthetic", "1")
        # UTF-16 surrogate range, not a workload limit.
        surrogates = "".join(map(chr, range(0xD800, 0xE000)))
        writer.point("text", {"text": surrogates, "ordinary": "مرحبا", "\ud800": "key"},
                     error={"kind": "synthetic", "message": "\ud800"})
        writer.point("continued")
        writer.close()
        rows = self.checked()
        text = rows[1]["data"]["text"]
        # The boundary between the two halves is the valid DBFF DC00 pair.
        # Every other code unit is lone; that pair becomes scalar U+10FC00.
        self.assertEqual(text, "\ufffd" * 1023 + "\U0010fc00" + "\ufffd" * 1023)
        self.assertFalse(any(0xD800 <= ord(char) < 0xE000 for char in text))
        self.assertEqual(rows[2]["event"], "continued")

    def test_only_close_can_end_root(self):
        for harmless in (False, True):
            with self.subTest(harmless=harmless):
                self.path = Path(self.temp.name) / f"root-{harmless}.jsonl"
                if harmless:
                    os.environ["FLATTRACE_DIR"] = str(Path(self.temp.name) / "env")
                    writer = Writer.from_env("synthetic", "1")
                    self.path = writer.path
                else:
                    writer = Writer(self.path, "synthetic", "1")
                child = writer.start("work")
                if harmless:
                    writer.end(writer.root, "ok")
                else:
                    with self.assertRaisesRegex(ValueError, "root.*close"):
                        writer.end(writer.root, "ok")
                self.assertIn(writer.root, writer.active)
                self.assertEqual(len(self.path.read_bytes().splitlines()), 2)
                writer.end(child, "ok")
                writer.close()
                self.checked()

    def test_payload_failures_degrade_and_continue(self):
        class Unserializable:
            def __repr__(self):
                raise RuntimeError("repr is not safe")

        cycle = {"kept": 7}
        cycle["self"] = cycle
        payloads = [
            {"bad": Unserializable(), "kept": 7, "notes": "caller"},
            {"bad": {1, 2}, "kept": 7},
            cycle,
            {("synthetic",): "kept"},
            ["nonobject", 7],
        ]
        writer = Writer(self.path, "synthetic", "1")
        for payload in payloads:
            writer.point("payload", payload)
        writer.point("error-context", {}, error={"kind": "synthetic", "message": "synthetic", "bad": Unserializable()})
        writer.point("continued", {"kept": 7})
        writer.close()
        rows = self.checked()
        for row in rows[1:-2]:
            self.assertIn("notes", row["data"])
        self.assertEqual(rows[1]["data"]["notes"][0], "caller")
        self.assertEqual(rows[1]["data"]["kept"], 7)
        self.assertEqual(rows[2]["data"]["kept"], 7)
        self.assertEqual(rows[-2]["event"], "continued")
        self.assertIs(cycle["self"], cycle)

    def test_mapping_subclasses_keep_their_json_shape(self):
        class Payload(dict):
            pass
        writer = Writer(self.path, "synthetic", "1", data=Payload(kept=7))
        writer.point("payload", Payload(kept=7),
                     error=Payload(kind="synthetic", message="synthetic", kept=7))
        writer.close()
        rows = self.checked()
        self.assertEqual(rows[0]["data"]["kept"], 7)
        self.assertEqual(rows[1]["data"], {"kept": 7})
        self.assertEqual(rows[1]["error"]["kept"], 7)

    def test_error_context_keys_do_not_erase_required_error_fields(self):
        writer = Writer(self.path, "synthetic", "1")
        writer.point("error-context", {}, error={"kind": "synthetic", "message": "synthetic", 1: "kept"})
        writer.point("continued")
        writer.close()
        row = self.checked()[1]
        self.assertEqual(row["error"]["kind"], "synthetic")
        self.assertEqual(row["error"]["message"], "synthetic")
        self.assertIn("kept", str(row["error"]))
        self.assertIn("notes", row["data"])

    def test_shared_argv_vectors(self):
        vectors = json.loads((ROOT / "tests/fixtures/sanitize-argv.json").read_text())
        for vector in vectors:
            with self.subTest(name=vector["name"]):
                self.assertEqual(sanitize_argv(vector["argv"]), vector["expected"])
                # The real run header must apply the same pass.
                path = Path(self.temp.name) / (vector["name"] + ".jsonl")
                writer = Writer(path, "synthetic", "1", argv=vector["argv"])
                writer.close()
                rows, issues, _ = read_trace(path)
                self.assertEqual(issues, [])
                self.assertEqual(rows[0]["data"]["argv"], vector["expected"])
