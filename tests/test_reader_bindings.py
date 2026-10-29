# SPDX-License-Identifier: MIT
"""Python bindings: engine-backed reader functions match the frozen goldens,
pure entry points keep working without the engine, and absence raises the
named EngineUnavailable."""
import json
import os
import sys
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(HERE, "conformance", "corpus")
GOLDEN = os.path.join(HERE, "conformance", "golden")

try:
    import flattrace._flattrace_read  # noqa: F401
    ENGINE = True
except ImportError:
    ENGINE = False

if os.environ.get("FLATTRACE_REQUIRE_ENGINE") == "1" and not ENGINE:
    # Release gate: a missing engine must fail the suite, never skip it.
    raise ImportError("FLATTRACE_REQUIRE_ENGINE=1 but flattrace._flattrace_read is not importable")

needs_engine = unittest.skipUnless(ENGINE, "reader engine not installed")


def corpus_files():
    return sorted(os.path.join(CORPUS, name) for name in os.listdir(CORPUS)
                  if os.path.isfile(os.path.join(CORPUS, name)))


class ReaderBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from flattrace import reader  # noqa: F401  (fails loudly without engine)
        cls.reader = reader

    def check_golden(self, name, actual):
        with open(os.path.join(GOLDEN, name), encoding="utf-8") as stream:
            expected = json.load(stream)
        self.assertEqual(_normalize(_rel(actual)), _normalize(expected))

    def test_pure_writer_importable_without_engine(self):
        import flattrace
        self.assertTrue(hasattr(flattrace, "Writer"))
        from flattrace import Writer  # noqa: F401

    def test_engine_absent_raises_named_error(self):
        import flattrace.reader
        blocked = sys.modules.pop("flattrace._flattrace_read", None)
        sys.modules["flattrace._flattrace_read"] = None
        try:
            with self.assertRaises(flattrace.reader.EngineUnavailable):
                flattrace.reader.digest(corpus_files())
        finally:
            if blocked is not None:
                sys.modules["flattrace._flattrace_read"] = blocked
            else:
                del sys.modules["flattrace._flattrace_read"]
        self.assertTrue(issubclass(flattrace.reader.EngineUnavailable, ImportError))

    @needs_engine
    def test_check(self):
        self.check_golden("check.json", self.reader.check(corpus_files()))

    @needs_engine
    def test_check_events(self):
        self.check_golden(
            "check_events.json",
            self.reader.check([os.path.join(CORPUS, "c01-parent.jsonl")],
                              [os.path.join(HERE, "conformance", "agent-events.json")]))

    @needs_engine
    def test_spans(self):
        files = corpus_files()
        self.check_golden("spans_all.json", self.reader.spans(files))
        cases = [
            ("spans_filtered.json", {"event": "tool.exec", "data": {"shot": "sh001"}}),
            ("spans_agent.json", {"agent": "synthetic-agent"}),
            ("spans_query.json", {"model": "synthetic-model-b", "outcome": "ok"}),
            ("spans_session.json", {"session": "session-7"}),
            ("spans_errors.json", {"error_kind": "io"}),
            ("spans_window.json", {"t_from": "2026-10-20T00:00:01.000Z",
                                   "t_to": "2026-10-20T00:00:01.050Z"}),
        ]
        for name, filt in cases:
            with self.subTest(name=name):
                self.check_golden(name, self.reader.spans(files, filt))

    @needs_engine
    def test_digest(self):
        self.check_golden("digest.json", self.reader.digest(corpus_files()))

    @needs_engine
    def test_group(self):
        files = corpus_files()
        self.check_golden("group.json", self.reader.group(files, ["tool", "event"]))
        self.check_golden("group_label.json",
                          self.reader.group(files, ["event", "data.shot"]))

    @needs_engine
    def test_tree(self):
        self.check_golden("tree.json",
                          self.reader.tree(corpus_files(), "a" * 32))

    @needs_engine
    def test_compare(self):
        self.check_golden(
            "compare.json",
            self.reader.compare([os.path.join(CORPUS, "c08-baseline.jsonl")],
                                [os.path.join(CORPUS, "c09-candidate.jsonl")]))

    @needs_engine
    def test_overlap(self):
        self.check_golden(
            "overlap.json",
            self.reader.overlap(corpus_files(), "loop.stall", {"event": "tool.exec"}))

    @needs_engine
    def test_spans_runend(self):
        rooted = [os.path.join(CORPUS, "closedata", "01.jsonl")]
        rows = self.reader.spans(rooted)
        self.check_golden("spans_runend.json", rows)
        run = next(r for r in rows if r["event"] == "run")
        self.assertEqual(run["end_data"],
                         {"turns": 3, "tokens": 42, "totals": {"took_ms": 40}})

    @needs_engine
    def test_parity_corpus(self):
        files = sorted(os.path.join(CORPUS, "parity", name)
                       for name in os.listdir(os.path.join(CORPUS, "parity")))
        self.check_golden("parity_digest.json", self.reader.digest(files))
        self.check_golden("parity_spans_model_nested.json",
                          self.reader.spans(files, {"model": "nested-m"}))
        self.assertEqual(self.reader.spans(files, {"model": "nested-m"}), [])
        matched = self.reader.spans(files, {"model": "env-m"})
        self.check_golden("parity_spans_model_empty.json", matched)
        self.assertEqual([(s["event"], s["span"]) for s in matched],
                         [("run", "1111111111111111"),
                          ("work", "2222222222222222")])

    @needs_engine
    def test_check_composition(self):
        comp = os.path.join(HERE, "conformance", "composition")
        with open(os.path.join(comp, "cases.json"),
                   encoding="utf-8") as stream:
            cases = json.load(stream)["cases"]
        actual = []
        for case in cases:
            events = [os.path.join(comp, name) for name in case["events"]]
            entry = dict(self.reader.check([os.path.join(comp, case["run"])],
                                            events, case.get("command"))[0])
            entry["file"] = case["run"]
            entry["events"] = list(case["events"])
            entry["issues"] = [s.replace(comp + "/", "")
                                 for s in entry["issues"]]
            entry["notices"] = [s.replace(comp + "/", "")
                                  for s in entry["notices"]]
            actual.append(entry)
        self.check_golden("check_composition.json", actual)

    @needs_engine
    def test_matches_reference_float_policy(self):
        digest = self.reader.digest(corpus_files())
        turn = next(t for t in digest["table"]
                    if (t["tool"], t["event"]) == ("agent", "agent.turn"))
        self.assertEqual(turn["took"], {"n": 1, "p50": 32, "p95": 32,
                                        "max": 32, "total": 32})
        self.assertEqual(turn["self"], {"n": 1, "p50": 2.0, "p95": 2.0,
                                        "max": 2.0, "total": 2.0})


EXACT_MESSAGES = frozenset([
    "missing envelope fields", "seq must be a nonnegative integer", "v must be 1",
    "must be a string", "invalid trace", "invalid span", "invalid parent",
    "run is reserved for lifetime spans", "invalid state or data", "t must be RFC3339 UTC",
    "end requires took_ms and outcome", "invalid took_ms", "took_ms and outcome belong on ends",
    "error requires string kind and message", "missing run start and end",
    "first line must be run start", "first valid line must be run start",
    "run start requires argv, host, version", "file contains different runs or tools",
    "missing run end", "missing final matching run end", "last line must be the matching run end",
    "expected one run start", "seq gap or reordering", "duplicate span start",
    "unexpected run start", "inactive parent", "unmatched end", "point outside active span",
    "unfinished span", "unfinished spans",
])


def _normalize(value):
    if isinstance(value, dict):
        if isinstance(value.get("issues"), list) and "rows" in value:
            value = dict(value)
            fixed = []
            for issue in value["issues"]:
                if isinstance(issue, str) and (issue.startswith("line ")
                                              or issue.startswith("incomplete tail ")):
                    end = issue.find(": ") + 2
                    if not any(m in issue[end:] for m in EXACT_MESSAGES):
                        issue = issue[:end]
                fixed.append(issue)
            value["issues"] = fixed
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def _rel(value):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k == "file" and isinstance(v, str) and os.path.isabs(v):
                try:
                    v = str(Path(v).relative_to(CORPUS))
                except ValueError:
                    pass
            out[k] = _rel(v)
        return out
    if isinstance(value, list):
        return [_rel(v) for v in value]
    return value


if __name__ == "__main__":
    unittest.main()
