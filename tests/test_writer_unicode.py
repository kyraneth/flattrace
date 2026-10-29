# SPDX-License-Identifier: MIT
"""Unicode policy through the public writer, not normalization helpers."""
import json
import tempfile
import unittest
from pathlib import Path

from flattrace import Writer
from flattrace.check import read_trace


class WriterUnicodeTests(unittest.TestCase):
    def test_unicode_entry_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unicode.jsonl"
            writer = Writer(path, "tool", "1")
            writer.point("pair\ud83d\ude00", {"text": "\ud83d\ude00"})
            writer.point("astral😀", {"text": "😀"})
            writer.point("data", {"text": "\ud800"})
            span = writer.start("envelope\ud800")
            writer.end(span, "ok")
            writer.point("keys", {"\ud800": "first", "�": "second"})
            class SurrogateRepr:
                def __repr__(self):
                    return "\ud800"
            writer.point("fallback", {"text": SurrogateRepr()})
            writer.close()
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            _, issues, _ = read_trace(path)
            self.assertEqual(issues, [])
            envelope_path = Path(directory) / "tool.jsonl"
            writer = Writer(envelope_path, "tool\ud800", "version\ud83d\ude00")
            writer.close()
            first = json.loads(envelope_path.read_text().splitlines()[0])
        note = "lone surrogate replaced with U+FFFD"
        self.assertEqual(first["tool"], "tool�")
        self.assertEqual(first["tool_version"], "version😀")
        self.assertIn(note, first["data"]["notes"])
        for row in rows[1:3]:
            self.assertEqual(row["data"], {"text": "😀"})
            self.assertTrue(row["event"].endswith("😀"))
        self.assertEqual(rows[3]["data"]["text"], "�")
        self.assertIn(note, rows[3]["data"]["notes"])
        for row in rows[4:6]:
            self.assertEqual(row["event"], "envelope�")
            self.assertIn(note, row["data"]["notes"])
        self.assertEqual(rows[7]["data"]["text"], "�")
        self.assertIn(note, rows[7]["data"]["notes"])
        self.assertIn("unserializable value replaced with text", rows[7]["data"]["notes"])
        self.assertEqual(rows[6]["data"]["�"], "first")
        self.assertIn("object keys collide after surrogate replacement", rows[6]["data"]["notes"])
