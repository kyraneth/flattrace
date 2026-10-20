# SPDX-License-Identifier: MIT
import gzip
from pathlib import Path
import tempfile
import unittest

from flattrace import Writer
from flattrace.check import read_trace

ROOT = Path(__file__).resolve().parents[1]


class GzipTests(unittest.TestCase):
    """Published <name>.jsonl.gz archives read exactly like the plain JSONL."""

    def setUp(self):
        (ROOT / "target").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "target")
        self.addCleanup(self.temp.cleanup)
        plain = Path(self.temp.name) / "run.jsonl"
        w = Writer(plain, "my-tool", "1.0", argv=["my-tool"])
        w.end(w.start("summary"), "ok")
        w.close()
        self.plain = plain
        self.raw = plain.read_bytes()

    def test_gzip_matches_plain(self):
        packed = self.plain.with_suffix(".jsonl.gz")
        packed.write_bytes(gzip.compress(self.raw, compresslevel=6))
        self.assertEqual(read_trace(packed), read_trace(self.plain))
        self.assertEqual(read_trace(packed)[1], [])

    def test_truncated_gzip_is_reported(self):
        packed = self.plain.with_suffix(".jsonl.gz")
        whole = gzip.compress(self.raw, compresslevel=6)
        packed.write_bytes(whole[: len(whole) // 2])
        rows, issues = read_trace(packed)
        self.assertTrue(any("gzip" in issue or "incomplete" in issue for issue in issues), issues)


if __name__ == "__main__":
    unittest.main()
