# SPDX-License-Identifier: MIT
"""Conformance: committed goldens reproduce the pure-Python reference exactly.

The Rust engine, CLI, Python and Node bindings assert these same files in
their own suites. Regenerate with tests/conformance/make_goldens.py and review
the diff; never hand-edit golden/ outputs.
"""
import json
import os
from pathlib import Path
import unittest

from tests.conformance import reference

HERE = Path(__file__).resolve().parent / "conformance"
CORPUS = HERE / "corpus"
GOLDEN = HERE / "golden"

BANNED = (b"/data" b"pool", b"kyra" b"-server", b"git.mario" b".garden", b":222" b"/")


def corpus_files():
    return sorted(str(p) for p in CORPUS.glob("*.jsonl*"))


def load_golden(name):
    return json.loads((GOLDEN / name).read_text())


class ConformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = os.getcwd()
        os.chdir(CORPUS)

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.saved)

    def check_golden(self, name, actual):
        self.assertEqual(actual, load_golden(name))

    def test_check(self):
        self.assertEqual(
            reference.check([os.path.basename(p) for p in corpus_files()]),
            load_golden("check.json"))

    def test_check_events(self):
        self.assertEqual(
            reference.check(["c01-parent.jsonl"], str(HERE / "agent-events.json")),
            load_golden("check_events.json"))

    def test_spans_all(self):
        self.check_golden("spans_all.json",
                          reference.spans([os.path.basename(p)
                                           for p in corpus_files()]))

    def test_spans_filtered(self):
        self.check_golden(
            "spans_filtered.json",
            reference.spans([os.path.basename(p) for p in corpus_files()],
                            {"event": "tool.exec", "data": {"shot": "sh001"}}))

    def test_spans_named_filters(self):
        files = [os.path.basename(p) for p in corpus_files()]
        for name, filt in (
                ("spans_agent.json", {"agent": "synthetic-agent"}),
                ("spans_query.json", {"model": "synthetic-model-b",
                                        "outcome": "ok"}),
                ("spans_session.json", {"session": "session-7"}),
                ("spans_errors.json", {"error_kind": "io"}),
                ("spans_window.json",
                 {"t_from": "2026-10-20T00:00:01.000Z",
                  "t_to": "2026-10-20T00:00:01.050Z"})):
            with self.subTest(name=name):
                self.check_golden(name, reference.spans(files, filt))

    def test_digest(self):
        self.check_golden("digest.json",
                          reference.digest([os.path.basename(p)
                                            for p in corpus_files()]))

    def test_group(self):
        self.check_golden("group.json",
                          reference.group([os.path.basename(p)
                                           for p in corpus_files()],
                                          ["tool", "event"]))

    def test_group_label(self):
        self.check_golden("group_label.json",
                          reference.group([os.path.basename(p)
                                           for p in corpus_files()],
                                          ["event", "data.shot"]))

    def test_tree(self):
        self.check_golden("tree.json",
                          reference.tree([os.path.basename(p)
                                          for p in corpus_files()], "a" * 32))

    def test_compare(self):
        self.check_golden(
            "compare.json",
            reference.compare(["c08-baseline.jsonl"], ["c09-candidate.jsonl"]))

    def test_overlap(self):
        self.check_golden(
            "overlap.json",
            reference.overlap([os.path.basename(p) for p in corpus_files()],
                              "loop.stall", {"event": "tool.exec"}))

    def test_real_samples_shape(self):
        real = sorted(str(p) for p in (CORPUS / "real").glob("*.jsonl"))
        self.assertEqual([os.path.basename(p) for p in real],
                         ["01.jsonl", "02.jsonl"])
        checked = reference.check(real)
        self.assertEqual(checked[0]["rows"], 39)
        self.assertEqual(checked[0]["issues"], [])
        self.assertTrue(any("missing run end" in i for i in checked[1]["issues"]))
        digest = reference.digest(real)
        tools = {(t["tool"], t["event"]) for t in digest["table"]}
        self.assertIn(("zamil", "eventloop.window"), tools)
        self.assertIn(("zamil", "session.refresh"), tools)
        window = next(t for t in digest["table"]
                      if (t["tool"], t["event"]) == ("zamil", "eventloop.window"))
        self.assertEqual(window["n"], 6)
        self.assertIsInstance(window["took"]["p50"], float)
        self.assertEqual(digest["coverage"]["runs"], 2)
        overlap = reference.overlap(real, "eventloop.window",
                                    {"event": "session.refresh"})
        self.assertTrue(any(w["overlapping"] for w in overlap["windows"]))

    def test_corpus_parses_and_stays_synthetic(self):
        names = sorted(p.name for p in CORPUS.glob("*.jsonl*"))
        self.assertEqual(names, [
            "c01-parent.jsonl", "c02-child.jsonl", "c03-async.jsonl",
            "c04-unfinished.jsonl", "c05-dup-a.jsonl", "c05-dup-b.jsonl",
            "c06-torn.jsonl", "c07-payload.jsonl", "c08-baseline.jsonl",
            "c09-candidate.jsonl", "c10-archive.jsonl.gz",
            "c11-bad.jsonl",
        ])
        checked = list(CORPUS.glob("*.jsonl*")) + list((CORPUS / "real").glob("*"))
        checked += [HERE / "agent-events.json"]
        checked += list(GOLDEN.glob("*.json"))
        self.assertTrue(len(checked) > 20)
        for path in checked:
            raw = path.read_bytes()
            for marker in BANNED:
                self.assertNotIn(marker, raw, f"{path.name} leaks {marker!r}")

    def test_golden_floats_compare_with_tolerance(self):
        # Parsed-JSON equality is exact; this pins the one known float
        # representation (self_ms) so engine ports match the type as well.
        digest = load_golden("digest.json")
        turn = next(t for t in digest["table"]
                    if (t["tool"], t["event"]) == ("agent", "agent.turn"))
        self.assertEqual(turn["took"], {"n": 1, "p50": 32, "p95": 32,
                                        "max": 32, "total": 32})
        self.assertEqual(turn["self"], {"n": 1, "p50": 2.0, "p95": 2.0,
                                        "max": 2.0, "total": 2.0})


if __name__ == "__main__":
    unittest.main()
