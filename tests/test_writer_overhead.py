# SPDX-License-Identifier: MIT
"""Payload/flush contracts exercised by the overhead optimizations."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flattrace import Writer
from flattrace.check import read_trace


class WriterPayloadTests(unittest.TestCase):
    def test_nested_normalization_never_mutates_caller_and_flushes_each_line(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            os.environ.pop("FLATTRACE_TRACEPARENT", None)
            path = Path(directory) / "synthetic.jsonl"
            writer = Writer(path, "synthetic", "1")
            payload = {"values": [1, (2, float("inf")), {"finite": 3.5}], "notes": "caller"}
            writer.point("sample", payload)
            raw = path.read_bytes()
            self.assertTrue(raw.endswith(b"\n"))
            self.assertEqual(len(raw.splitlines()), 2)
            row = json.loads(raw.splitlines()[-1])
            self.assertEqual(row["data"]["values"], [1, [2, None], {"finite": 3.5}])
            self.assertEqual(row["data"]["notes"], ["caller", "non-finite number replaced with null"])
            self.assertEqual(payload["notes"], "caller")
            self.assertIsInstance(payload["values"][1], tuple)
            self.assertEqual(payload["values"][1][1], float("inf"))
            writer.point("unicode", {"text": "مرحبا", "path": os.fsdecode(b"synthetic-\xff")})
            row = json.loads(path.read_bytes().splitlines()[-1])
            self.assertEqual(row["data"], {"text": "مرحبا", "path": "synthetic-\ufffd",
                                              "notes": ["lone surrogate replaced with U+FFFD"]})
            writer.close()
            self.assertFalse(read_trace(path)[1])
