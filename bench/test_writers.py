# SPDX-License-Identifier: MIT
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("writer_bench", Path(__file__).with_name("writers.py"))
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


class BenchContracts(unittest.TestCase):
    def test_inputs_are_explicit_and_positive(self):
        for raw in ("0", "-1"):
            with self.assertRaises(argparse.ArgumentTypeError):
                bench.positive(raw)
        result = subprocess.run([sys.executable, str(Path(bench.__file__))], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--iterations", result.stderr)
        self.assertIn("--repeats", result.stderr)

    def test_streamed_readback_detects_seq_payload_pairing_and_missing_line(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            adapter = Path(__file__).with_name("python_writer.py")
            for mode in ("off", "0", "1", "2"):
                out = directory / mode
                out.mkdir()
                subprocess.run([sys.executable, str(adapter), "1", mode, str(out), "1"],
                               capture_output=True, check=True)
                self.assertEqual(bench.verify(out, 1, mode), {"off": 0, "0": 0, "1": 3, "2": 4}[mode])
            path = next((directory / "1").iterdir())
            original = [json.loads(line) for line in path.read_text().splitlines()]
            for mutation in ("seq", "payload", "pairing", "missing"):
                rows = copy.deepcopy(original)
                if mutation == "seq":
                    rows[1]["seq"] += 1
                elif mutation == "payload":
                    rows[1]["data"]["count"] += 1
                elif mutation == "pairing":
                    rows[-2]["event"] = "wrong-end"
                    rows[-2]["parent"] = "abcdefabcdefabcd"
                else:
                    rows.pop()
                path.write_text("".join(json.dumps(row) + "\n" for row in rows))
                with self.assertRaises(AssertionError):
                    bench.verify(directory / "1", 1, "1")

    def test_context_is_allowlisted(self):
        context = bench.context()
        self.assertEqual(set(context), {"utc", "loadavg", "cpu_ticks", "pressure", "memory"})
        self.assertEqual(set(context["memory"]), {"MemTotal_KiB", "MemAvailable_KiB", "SwapTotal_KiB", "SwapFree_KiB"})
