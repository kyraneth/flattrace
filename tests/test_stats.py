# SPDX-License-Identifier: MIT
import gzip
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from flattrace.stats import analyse

TRACE = "1" * 32


def row(seq, event, state, span, parent=None, took=None, data=None, **extra):
    value = dict(v=1, seq=seq, tool="synthetic", tool_version="1", run="sample",
                 trace=TRACE, span=f"{span:016x}", parent=None if parent is None else f"{parent:016x}",
                 event=event, state=state, t="2026-01-01T00:00:00Z", data=data or {})
    if state == "end":
        value.update(took_ms=took, outcome="ok")
    value.update(extra)
    return value


def root():
    return row(0, "run", "start", 1, data=dict(argv=[], host="synthetic", version="1", level=2))


class StatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, rows, name="trace.jsonl"):
        path = self.dir / name
        text = "".join(json.dumps(value) + "\n" for value in rows)
        if name.endswith(".gz"):
            path.write_bytes(gzip.compress(text.encode()))
        else:
            path.write_text(text)
        return path

    def sample(self):
        return [root(), row(1, "outer", "start", 2, 1), row(2, "inner", "start", 3, 2),
                row(3, "inner", "end", 3, 2, 20), row(4, "outer", "end", 2, 1, 60),
                row(5, "warning", "point", 1, data=dict(severity="warning", count=3)),
                row(6, "failure", "point", 1, error=dict(kind="synthetic", message="sample")),
                row(7, "run", "end", 1, took=100)]

    def test_real_cli_text_json_and_nested_self(self):
        path = self.write(self.sample())
        command = [sys.executable, "-m", "flattrace", "stats", str(self.dir)]
        result = subprocess.run(command + ["--json"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["run_count"], 1)
        self.assertEqual(report["levels"], {"2": 1})
        self.assertEqual(report["spans"]["outer"]["self_ms"]["total"], 40)
        self.assertEqual(report["spans"]["run"]["self_ms"]["total"], 40)
        self.assertEqual(report["runs"][0]["dominant_spans"], ["outer", "inner"])
        self.assertEqual(report["errors_by_name"], {"failure": 1})
        self.assertEqual(report["warnings_by_name"], {"warning": 3})
        text = subprocess.run(command, capture_output=True, text=True, check=True).stdout
        for part in ("Runs: 1", "outer: n=1 p50=60 p95=60 max=60 total=60", "self: n=1", str(path)):
            self.assertIn(part, text)

    def test_exact_quantiles_cross_run_aggregation_and_dedup(self):
        for n, duration in enumerate([1, 2, 3, 4, 100]):
            rows = [root(), row(1, "work", "start", 2, 1), row(2, "work", "end", 2, 1, duration),
                    row(3, "run", "end", 1, took=duration + 1)]
            for value in rows:
                value["run"] = f"sample-{n}"
            self.write(rows, f"{n}.jsonl")
            if n == 0:
                self.write(rows, "duplicate.jsonl.gz")
        report = analyse([self.dir])
        self.assertEqual(report["run_count"], 5)
        self.assertEqual(len(report["duplicates"]), 1)
        self.assertEqual(report["spans"]["work"]["total_ms"], dict(n=5, p50=3, p95=100, max=100, total=110))
        self.assertEqual(report["runs"][report["slowest_runs"][0]]["took_ms"], 101)

    def test_overlapping_children_do_not_invent_self(self):
        rows = [root(), row(1, "work", "start", 2, 1), row(2, "work", "start", 3, 1),
                row(3, "work", "end", 2, 1, 50), row(4, "work", "end", 3, 1, 40),
                row(5, "run", "end", 1, took=100)]
        report = analyse([self.write(rows)])
        self.assertIsNone(report["spans"]["run"]["self_ms"])
        self.assertEqual(report["runs"][0]["self_unknown"], {"run": {"overlapping children": 1}})
        self.assertEqual(report["spans"]["work"]["self_ms"]["total"], 90)

    def test_async_descendants_invalidate_ancestor_self(self):
        rows = [root(), row(1, "outer", "start", 2, 1), row(2, "inner", "start", 3, 2),
                row(3, "outer", "end", 2, 1, 30), row(4, "inner", "end", 3, 2, 50),
                row(5, "run", "end", 1, took=100)]
        report = analyse([self.write(rows)])
        self.assertIsNone(report["spans"]["outer"]["self_ms"])
        self.assertIsNone(report["spans"]["run"]["self_ms"])
        self.assertEqual(report["spans"]["inner"]["self_ms"]["total"], 50)
        self.assertFalse(report["runs"][0]["issues"])

    def test_null_duration_and_legacy_levels_remain_unknown(self):
        rows = [root(), row(1, "work", "start", 2, 1), row(2, "work", "end", 2, 1),
                row(3, "run", "end", 1, took=100)]
        del rows[0]["data"]["level"]
        for value in rows:
            del value["seq"]
        report = analyse([self.write(rows)])
        self.assertIsNone(report["spans"]["work"]["total_ms"])
        self.assertIsNone(report["spans"]["run"]["self_ms"])
        self.assertEqual(report["levels"], {"unknown": 1})

    def test_torn_tail_bad_seq_and_empty_input_are_diagnosed(self):
        path = self.write([root(), row(1, "work", "start", 2, 1)])
        with path.open("ab") as file:
            file.write(b'{"unfinished":')
        result = subprocess.run([sys.executable, "-m", "flattrace", "stats", str(path), "--json"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual(report["statuses"], {"unfinished": 1})
        self.assertEqual(report["runs"][0]["unfinished_spans"], {"run": 1, "work": 1})
        self.assertIn("incomplete tail", " ".join(report["runs"][0]["issues"]))
        rows = self.sample()
        rows[2]["seq"] = 100
        report = analyse([self.write(rows)])
        self.assertIsNone(report["spans"]["outer"]["self_ms"])
        self.assertIn("seq gap", " ".join(report["runs"][0]["issues"]))
        empty = analyse([self.write([], "empty.jsonl")])
        self.assertEqual(empty["run_count"], 0)
        self.assertEqual(len(empty["invalid_files"]), 1)

    def test_interior_corruption_mixed_runs_and_unmatched_end(self):
        rows = [root(), row(1, "work", "end", 2, 1, 10), row(2, "run", "end", 1, took=100)]
        path = self.write(rows)
        with path.open("a") as file:
            file.write("not JSON\n")
        report = analyse([path])
        self.assertIn("unmatched end", " ".join(report["runs"][0]["issues"]))
        self.assertNotIn("work", report["spans"])
        rows[1]["run"] = "other"
        report = analyse([self.write(rows)])
        self.assertIn("different runs", " ".join(report["runs"][0]["issues"]))

    def test_gzip_archive_damage_and_io_error_exit(self):
        path = self.write(self.sample(), "trace.jsonl.gz")
        path.write_bytes(path.read_bytes()[:-4])  # gzip trailer CRC/size evidence
        report = analyse([path])
        self.assertIn("incomplete gzip", " ".join(report["runs"][0]["issues"]))
        missing = subprocess.run([sys.executable, "-m", "flattrace", "stats", str(self.dir / "missing.jsonl")],
                                 capture_output=True, text=True)
        self.assertEqual(missing.returncode, 2)

    def test_invalid_gzip_deflate_is_reported_without_aborting_corpus(self):
        damaged = bytearray(gzip.compress(b"synthetic"))
        damaged[10] = 255  # DEFLATE reserved block type, after the gzip header.
        bad = self.dir / "corrupt.jsonl.gz"
        bad.write_bytes(damaged)
        good = self.write(self.sample())
        result = subprocess.run([sys.executable, "-m", "flattrace", "stats", str(bad), str(good), "--json"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["run_count"], 1)
        self.assertIn("corrupt gzip archive", " ".join(report["invalid_files"][0]["issues"]))

    def test_duplicate_corruption_is_not_hidden_or_double_counted(self):
        first = self.write(self.sample())
        second = self.write(self.sample(), "duplicate.jsonl")
        with second.open("a") as stream:
            stream.write("BAD\n")
        result = subprocess.run([sys.executable, "-m", "flattrace", "stats", str(first), str(second), "--json"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual(report["run_count"], 1)
        self.assertEqual(report["spans"]["outer"]["total_ms"]["n"], 1)
        self.assertEqual(report["errors_by_name"], {"failure": 1})
        self.assertEqual(report["warnings_by_name"], {"warning": 3})
        self.assertEqual(report["duplicates"][0]["issue_counts"], {"line": 1})

    def test_repeated_corruption_diagnostics_are_aggregated(self):
        path = self.write(self.sample())
        with path.open("a") as stream:
            for _ in self.sample():
                stream.write("BAD\n")
        report = analyse([path])
        run = report["runs"][0]
        self.assertEqual(run["issue_counts"], {"line": len(self.sample())})
        self.assertEqual(len(run["issues"]), 1)
        self.assertIn("occurrences: 8", run["issues"][0])

    def test_no_files_and_repeated_path(self):
        with self.assertRaisesRegex(ValueError, "no .jsonl"):
            analyse([self.dir])
        path = self.write(self.sample())
        self.assertEqual(analyse([path, path, self.dir])["files"], 1)


if __name__ == "__main__":
    unittest.main()
