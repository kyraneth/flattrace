# SPDX-License-Identifier: MIT
"""Build the 0.3.0 checker-composition conformance fixtures (synthetic only).

Run: python3 tests/conformance/make_composition.py
Output: tests/conformance/composition/*.json + *.jsonl + cases.json.
Every ID, timestamp and hash is fixed by this script except the manifest
sha256 values, which are computed over the exact vocabulary bytes just
written; reruns are byte-identical. cases.json maps each run to the event
files (and optional --command) its golden was generated with, so the Rust,
CLI, Python and Node checkers assert the same cases.
"""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMP = HERE / "composition"

T0 = "2026-10-27T00:00:00.000Z"
T1 = "2026-10-27T00:00:01.000Z"
T2 = "2026-10-27T00:00:02.000Z"
T3 = "2026-10-27T00:00:03.000Z"
T4 = "2026-10-27T00:00:04.000Z"
T5 = "2026-10-27T00:00:05.000Z"
T6 = "2026-10-27T00:00:06.000Z"
T7 = "2026-10-27T00:00:07.000Z"
T8 = "2026-10-27T00:00:08.000Z"

RESERVED_RUN = {"kind": "span", "fields": {}}


def vocab(tool, events, **extra):
    return {"tool": tool, "events": {"run": RESERVED_RUN, **events}, **extra}


def write_vocab(name, document):
    path = COMP / name
    path.write_bytes(json.dumps(document, indent=2, sort_keys=True).encode("utf-8") + b"\n")
    return path


def sha_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_entry(tool, path, wrong=False):
    return {"tool": tool, "version": "0.3.0",
            "sha256": "0" * 64 if wrong else sha_of(path)}


def row(seq, tool, version, run, trace, span, parent, event, state, t,
        data=None, took=None, outcome=None):
    value = {"v": 1, "seq": seq, "tool": tool, "tool_version": version,
             "run": run, "trace": trace, "span": span, "parent": parent,
             "event": event, "state": state, "t": t, "data": data or {}}
    if state == "end":
        value["took_ms"] = took
        value["outcome"] = outcome
    return value


def run_start(seq, tool, version, run, trace, span, t, data):
    header = {"argv": ["synthetic"], "host": "synthetic", "version": version,
              "pid": 4242, **data}
    return row(seq, tool, version, run, trace, span, None,
               "run", "start", t, header)


def write_run(name, rows):
    path = COMP / name
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def main():
    COMP.mkdir(exist_ok=True)
    for path in COMP.iterdir():
        path.unlink()

    rec_path = write_vocab("rec.json", vocab("recorder", {
        "work": {"kind": "span", "fields": {"item": "work item label"}},
        "action": {"kind": "point", "fields": {"kind": "observed operation kind"}},
        "changes": {"kind": "point", "fields": {"source": "git or fs delta source"}},
    }, required=["work"]))
    plug_path = write_vocab("plug.json", vocab("plugin", {
        "annotate": {"kind": "span", "fields": {"detector": "calling detector identity"}},
        "annotation": {"kind": "point", "fields": {"label": "detector suggestion label"}},
        "verdict": {"kind": "point", "fields": {"reason": "grader explanation"}},
    }))
    conf_a_path = write_vocab("conf-a.json", vocab("conf-a", {
        "shared": {"kind": "span", "fields": {"detail": "shared shape, first wording"}},
        "typed": {"kind": "span", "fields": {}},
    }))
    conf_b_path = write_vocab("conf-b.json", vocab("conf-b", {
        # Description-only change on shared, kind change on typed.
        "shared": {"kind": "span", "fields": {"detail": "shared shape, second wording"}},
        "typed": {"kind": "point", "fields": {}},
    }))
    same_a_path = write_vocab("same-a.json", vocab("recorder", {
        "work": {"kind": "span", "fields": {"item": "work item label"}},
    }))
    same_b_path = write_vocab("same-b.json", vocab("recorder", {
        "work": {"kind": "span", "fields": {"item": "work item label"}},
        "extra": {"kind": "point", "fields": {}},
    }))
    export_path = write_vocab("export.json", vocab("exporter", {
        "journal.export": {"kind": "point", "fields": {"entryId": "frozen journal entry ID"}},
    }))
    cmda_path = write_vocab("cmda.json", vocab("cmda", {
        "a.fetch": {"kind": "point", "fields": {"source": "fetch source locator"}},
    }, commands={"capture": ["a.fetch"]}))
    cmdb_path = write_vocab("cmdb.json", vocab("cmdb", {
        "b.tile": {"kind": "point", "fields": {"tile": "render tile identity"}},
    }, commands={"render": ["b.tile"]}))
    dup_a_path = write_vocab("dup-a.json", vocab("dup-a", {
        "da.step": {"kind": "span", "fields": {}},
    }, commands={"build": ["da.step"]}))
    dup_b_path = write_vocab("dup-b.json", vocab("dup-b", {
        "db.step": {"kind": "span", "fields": {}},
    }, commands={"build": ["db.step"]}))
    conv_tool_path = write_vocab("conv-tool.json", vocab("conv-tool", {
        "stage": {"kind": "span", "fields": {"phase": "pipeline phase label"}},
    }))
    conv_differ_path = write_vocab("conv-differ.json", vocab("differ", {
        # Same name as the conventions annotation, different definition.
        "annotation": {"kind": "point", "fields": {
            "label": "a different wording for the same suggestion"}},
    }))
    conv_events = "../../../schema/conventions-events.json"
    conv_sha = hashlib.sha256(
        (HERE.parents[1] / "schema" / "conventions-events.json").read_bytes()
    ).hexdigest()

    def conv_manifest_entry(tool, path):
        if path == conv_events:
            return {"tool": tool, "version": "0.3.0", "sha256": conv_sha}
        return manifest_entry(tool, path)

    cases = []
    trace_no = [0]

    def trace():
        trace_no[0] += 1
        return f"{trace_no[0]:032x}"

    def span(n):
        return f"{n:016x}"

    def manifest(*entries):
        return {"vocabularies": list(entries)}

    # comp-pass: the passing two-vocabulary run, with additive convention
    # fields (vocabularies manifest, links, gen_ai usage, annotation target).
    tid, run = trace(), "comp-pass"
    work, annotate, code = span(2), span(3), span(4)
    rows = [run_start(0, "recorder", "0.3.0", run, tid, span(1), T0, {
        **manifest(manifest_entry("recorder", rec_path),
                   manifest_entry("plugin", plug_path)),
        "links": [{"relation": "continues_process", "trace": "0" * 32,
                   "span": span(9)}],
        "env": {"gen_ai.request.model": "synthetic-model",
                "gen_ai.usage": {"input_tokens": 12, "output_tokens": 3}}})]
    rows.append(row(1, "recorder", "0.3.0", run, tid, work, span(1),
                    "work", "start", T1, {"item": "first"}))
    rows.append(row(2, "recorder", "0.3.0", run, tid, annotate, work,
                    "annotate", "start", T2,
                    {"detector": {"name": "shape", "version": "1",
                                  "kind": "code"},
                     "questions": ["repeated-call?"], "input_sha256": "1" * 64,
                     "usage": None, "cost": None}))
    rows.append(row(3, "recorder", "0.3.0", run, tid, annotate, work,
                    "annotation", "point", T3,
                    {"target": {"trace": tid, "run": run, "span": work,
                                "seq": 1},
                     "id": "ann-1",
                     "detector": {"name": "shape", "version": "1",
                                  "kind": "code"},
                     "label": "repeated-call", "probability": None,
                     "input_sha256": "1" * 64}))
    rows.append(row(4, "recorder", "0.3.0", run, tid, annotate, work,
                    "annotation", "point", T4,
                    {"target": {"trace": tid, "run": run, "span": work,
                                "seq": 1},
                     "id": "ann-2", "supersedes": "ann-1",
                     "detector": {"name": "shape", "version": "1",
                                  "kind": "code"},
                     "label": "paging-not-retry", "probability": None,
                     "input_sha256": "1" * 64}))
    rows.append(row(5, "recorder", "0.3.0", run, tid, annotate, work,
                    "annotate", "end", T5, {}, took=2, outcome="ok"))
    rows.append(row(6, "recorder", "0.3.0", run, tid, work, span(1),
                    "action", "point", T5,
                    {"kind": "exec", "paths": ["scene.blend"],
                     "bytes_changed": None, "lines_added": None,
                     "lines_removed": None, "argv": ["render", "scene.blend"],
                     "exit_code": 0}))
    rows.append(row(7, "recorder", "0.3.0", run, tid, span(1), None,
                    "annotation", "point", T6,
                    {"target": {"trace": tid, "run": run, "span": span(1)},
                     "id": "ann-3",
                     "detector": {"name": "shape", "version": "1",
                                  "kind": "code"},
                     "label": "run-shape-ok", "probability": None,
                     "input_sha256": "1" * 64}))
    rows.append(row(8, "recorder", "0.3.0", run, tid, work, span(1),
                    "work", "end", T6, {}, took=5, outcome="ok"))
    rows.append(row(9, "recorder", "0.3.0", run, tid, span(1), None,
                    "changes", "point", T7,
                    {"files": [{"path": "scene.blend", "added": 3,
                                "removed": 1}],
                     "source": "git", "coverage": "ignored paths unseen"}))
    rows.append(row(10, "recorder", "0.3.0", run, tid, span(1), None,
                    "verdict", "point", T7,
                    {"trace": tid, "run": run, "name": "shape-grade",
                     "correct": True, "score": 1, "reward": None,
                     "reason": "paging, not a retry",
                     "grader": {"name": "grade", "version": "1",
                                "kind": "code"}}))
    rows.append(row(11, "recorder", "0.3.0", run, tid, span(1), None,
                    "run", "end", T8, {}, took=8, outcome="ok"))
    write_run("comp-pass.jsonl", rows)
    cases.append({"run": "comp-pass.jsonl",
                  "events": ["rec.json", "plug.json"]})

    # comp-undeclared: one event no vocabulary declares.
    tid, run = trace(), "comp-undeclared"
    rows = [run_start(0, "recorder", "0.3.0", run, tid, span(1), T0, {
        **manifest(manifest_entry("recorder", rec_path),
                   manifest_entry("plugin", plug_path))})]
    rows.append(row(1, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "work", "start", T1, {}))
    rows.append(row(2, "recorder", "0.3.0", run, tid, span(1), None,
                    "rogue.event", "point", T2, {}))
    rows.append(row(3, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "work", "end", T3, {}, took=1, outcome="ok"))
    rows.append(row(4, "recorder", "0.3.0", run, tid, span(1), None,
                    "run", "end", T4, {}, took=4, outcome="ok"))
    write_run("comp-undeclared.jsonl", rows)
    cases.append({"run": "comp-undeclared.jsonl",
                  "events": ["rec.json", "plug.json"]})

    # comp-conflict-def: same name, different parsed definitions
    # (shared differs by description only; typed differs by kind).
    tid, run = trace(), "comp-conflict-def"
    rows = [run_start(0, "recorder", "0.3.0", run, tid, span(1), T0, {
        **manifest(manifest_entry("conf-a", conf_a_path),
                   manifest_entry("conf-b", conf_b_path))})]
    rows.append(row(1, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "shared", "start", T1, {}))
    rows.append(row(2, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "shared", "end", T2, {}, took=1, outcome="ok"))
    rows.append(row(3, "recorder", "0.3.0", run, tid, span(3), span(1),
                    "typed", "start", T3, {}))
    rows.append(row(4, "recorder", "0.3.0", run, tid, span(3), span(1),
                    "typed", "end", T4, {}, took=1, outcome="ok"))
    rows.append(row(5, "recorder", "0.3.0", run, tid, span(1), None,
                    "run", "end", T5, {}, took=5, outcome="ok"))
    write_run("comp-conflict-def.jsonl", rows)
    cases.append({"run": "comp-conflict-def.jsonl",
                  "events": ["conf-a.json", "conf-b.json"]})

    # comp-sametool: two different-bytes files claiming the same tool.
    tid, run = trace(), "comp-sametool"
    rows = [run_start(0, "recorder", "0.3.0", run, tid, span(1), T0, {
        **manifest(manifest_entry("recorder", same_a_path),
                   manifest_entry("recorder", same_b_path))})]
    rows.append(row(1, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "work", "start", T1, {}))
    rows.append(row(2, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "work", "end", T2, {}, took=1, outcome="ok"))
    rows.append(row(3, "recorder", "0.3.0", run, tid, span(1), None,
                    "run", "end", T3, {}, took=3, outcome="ok"))
    write_run("comp-sametool.jsonl", rows)
    cases.append({"run": "comp-sametool.jsonl",
                  "events": ["same-a.json", "same-b.json"]})

    # comp-identical: the same file twice is one vocabulary, not a collision.
    tid, run = trace(), "comp-identical"
    rows = [run_start(0, "recorder", "0.3.0", run, tid, span(1), T0,
                      {"argv": ["synthetic"], "host": "synthetic",
                       "version": "0.3.0", "pid": 4242})]
    rows.append(row(1, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "work", "start", T1, {}))
    rows.append(row(2, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "work", "end", T2, {}, took=1, outcome="ok"))
    rows.append(row(3, "recorder", "0.3.0", run, tid, span(1), None,
                    "run", "end", T3, {}, took=3, outcome="ok"))
    write_run("comp-identical.jsonl", rows)
    cases.append({"run": "comp-identical.jsonl",
                  "events": ["rec.json", "rec.json"]})

    # comp-hash: the manifest hash does not match the supplied file.
    tid, run = trace(), "comp-hash"
    rows = [run_start(0, "recorder", "0.3.0", run, tid, span(1), T0, {
        **manifest(manifest_entry("recorder", rec_path, wrong=True))})]
    rows.append(row(1, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "work", "start", T1, {}))
    rows.append(row(2, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "work", "end", T2, {}, took=1, outcome="ok"))
    rows.append(row(3, "recorder", "0.3.0", run, tid, span(1), None,
                    "run", "end", T3, {}, took=3, outcome="ok"))
    write_run("comp-hash.jsonl", rows)
    cases.append({"run": "comp-hash.jsonl", "events": ["rec.json"]})

    # comp-extra-journal: an unlisted file cannot legalize journal.* in a run.
    tid, run = trace(), "comp-extra-journal"
    rows = [run_start(0, "recorder", "0.3.0", run, tid, span(1), T0, {
        **manifest(manifest_entry("recorder", rec_path))})]
    rows.append(row(1, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "work", "start", T1, {}))
    rows.append(row(2, "recorder", "0.3.0", run, tid, span(1), None,
                    "journal.export", "point", T2, {"entryId": "e1"}))
    rows.append(row(3, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "work", "end", T3, {}, took=1, outcome="ok"))
    rows.append(row(4, "recorder", "0.3.0", run, tid, span(1), None,
                    "run", "end", T4, {}, took=4, outcome="ok"))
    write_run("comp-extra-journal.jsonl", rows)
    cases.append({"run": "comp-extra-journal.jsonl",
                  "events": ["rec.json", "export.json"]})

    # comp-foreign-pass: owner A's required event does not fail owner B's
    # command (render needs b.tile only; a.fetch is absent and unrequired).
    # Control: comp-foreign-fail misses A's own required event for capture.
    for name in ("comp-foreign-pass", "comp-foreign-fail"):
        tid, run = trace(), name
        command = "render" if name == "comp-foreign-pass" else "capture"
        rows = [run_start(0, "recorder", "0.3.0", run, tid, span(1), T0, {
            **manifest(manifest_entry("cmda", cmda_path),
                       manifest_entry("cmdb", cmdb_path)),
            "command": command})]
        rows.append(row(1, "recorder", "0.3.0", run, tid, span(1), None,
                        "b.tile", "point", T1, {"tile": "t"}))
        rows.append(row(2, "recorder", "0.3.0", run, tid, span(1), None,
                        "run", "end", T2, {}, took=2, outcome="ok"))
        write_run(f"{name}.jsonl", rows)
        cases.append({"run": f"{name}.jsonl",
                      "events": ["cmda.json", "cmdb.json"]})

    # comp-dup-command: one command ID in two files.
    tid, run = trace(), "comp-dup-command"
    rows = [run_start(0, "recorder", "0.3.0", run, tid, span(1), T0, {
        **manifest(manifest_entry("dup-a", dup_a_path),
                   manifest_entry("dup-b", dup_b_path)),
        "command": "build"})]
    rows.append(row(1, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "da.step", "start", T1, {}))
    rows.append(row(2, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "da.step", "end", T2, {}, took=1, outcome="ok"))
    rows.append(row(3, "recorder", "0.3.0", run, tid, span(3), span(1),
                    "db.step", "start", T3, {}))
    rows.append(row(4, "recorder", "0.3.0", run, tid, span(3), span(1),
                    "db.step", "end", T4, {}, took=1, outcome="ok"))
    rows.append(row(5, "recorder", "0.3.0", run, tid, span(1), None,
                    "run", "end", T5, {}, took=5, outcome="ok"))
    write_run("comp-dup-command.jsonl", rows)
    cases.append({"run": "comp-dup-command.jsonl",
                  "events": ["dup-a.json", "dup-b.json"]})

    # comp-no-manifest: composed files, no manifest: legacy pass + notice.
    tid, run = trace(), "comp-no-manifest"
    rows = [run_start(0, "recorder", "0.3.0", run, tid, span(1), T0, {})]
    rows.append(row(1, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "work", "start", T1, {}))
    rows.append(row(2, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "annotation", "point", T2,
                    {"target": {"trace": tid, "run": run},
                     "id": "ann-1",
                     "detector": {"name": "shape", "version": "1",
                                  "kind": "code"},
                     "label": "run-shape-ok", "probability": None,
                     "input_sha256": "1" * 64}))
    rows.append(row(3, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "work", "end", T3, {}, took=1, outcome="ok"))
    rows.append(row(4, "recorder", "0.3.0", run, tid, span(1), None,
                    "run", "end", T4, {}, took=4, outcome="ok"))
    write_run("comp-no-manifest.jsonl", rows)
    cases.append({"run": "comp-no-manifest.jsonl",
                  "events": ["rec.json", "plug.json"]})

    # comp-envelope: one file, envelope tool mismatch (legacy failure stays).
    tid, run = trace(), "comp-envelope"
    rows = [run_start(0, "other-tool", "0.3.0", run, tid, span(1), T0, {})]
    rows.append(row(1, "other-tool", "0.3.0", run, tid, span(2), span(1),
                    "work", "start", T1, {}))
    rows.append(row(2, "other-tool", "0.3.0", run, tid, span(2), span(1),
                    "work", "end", T2, {}, took=1, outcome="ok"))
    rows.append(row(3, "other-tool", "0.3.0", run, tid, span(1), None,
                    "run", "end", T3, {}, took=3, outcome="ok"))
    write_run("comp-envelope.jsonl", rows)
    cases.append({"run": "comp-envelope.jsonl", "events": ["rec.json"]})

    # comp-dup-seq: discontinuous sequence counters stay an error.
    tid, run = trace(), "comp-dup-seq"
    rows = [run_start(0, "recorder", "0.3.0", run, tid, span(1), T0, {
        **manifest(manifest_entry("recorder", rec_path))})]
    rows.append(row(1, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "work", "start", T1, {}))
    rows.append(row(1, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "work", "end", T2, {}, took=1, outcome="ok"))
    rows.append(row(3, "recorder", "0.3.0", run, tid, span(1), None,
                    "run", "end", T3, {}, took=3, outcome="ok"))
    write_run("comp-dup-seq.jsonl", rows)
    cases.append({"run": "comp-dup-seq.jsonl", "events": ["rec.json"]})

    # comp-unknown: null durations stay unknown, never zero-filled or failed.
    tid, run = trace(), "comp-unknown"
    rows = [run_start(0, "recorder", "0.3.0", run, tid, span(1), T0, {
        **manifest(manifest_entry("recorder", rec_path))})]
    rows.append(row(1, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "work", "start", T1, {}))
    rows.append(row(2, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "work", "end", T2, {}, took=None, outcome="unknown"))
    rows.append(row(3, "recorder", "0.3.0", run, tid, span(1), None,
                    "run", "end", T3, {}, took=None, outcome="unknown"))
    write_run("comp-unknown.jsonl", rows)
    cases.append({"run": "comp-unknown.jsonl", "events": ["rec.json"]})

    # comp-surrogate: a legacy lone-surrogate escape plus a valid pair.
    tid, run = trace(), "comp-surrogate"
    rows = [run_start(0, "recorder", "0.3.0", run, tid, span(1), T0, {
        **manifest(manifest_entry("recorder", rec_path))})]
    rows.append(row(1, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "work", "start", T1, {"note": "lone: \ud800 pair: \U0001F600"}))
    rows.append(row(2, "recorder", "0.3.0", run, tid, span(2), span(1),
                    "work", "end", T2, {}, took=1, outcome="ok"))
    rows.append(row(3, "recorder", "0.3.0", run, tid, span(1), None,
                    "run", "end", T3, {}, took=3, outcome="ok"))
    write_run("comp-surrogate.jsonl", rows)
    cases.append({"run": "comp-surrogate.jsonl", "events": ["rec.json"]})

    # comp-conv-pass: an annotate span with annotation points, validated
    # against the published conventions vocabulary plus one tool file.
    tid, run = trace(), "comp-conv-pass"
    rows = [run_start(0, "conv-tool", "0.3.0", run, tid, span(1), T0, {
        **manifest(conv_manifest_entry("flattrace-conventions", conv_events),
                   conv_manifest_entry("conv-tool", conv_tool_path))})]
    rows.append(row(1, "conv-tool", "0.3.0", run, tid, span(2), span(1),
                    "stage", "start", T1, {"phase": "review"}))
    rows.append(row(2, "conv-tool", "0.3.0", run, tid, span(3), span(2),
                    "annotate", "start", T2,
                    {"detector": {"name": "shape", "version": "1",
                                  "kind": "code"},
                     "questions": ["repeated-call?"], "input_sha256": "1" * 64,
                     "usage": None, "cost": None}))
    rows.append(row(3, "conv-tool", "0.3.0", run, tid, span(3), span(2),
                    "annotation", "point", T3,
                    {"target": {"trace": tid, "run": run, "span": span(2),
                                "seq": 1},
                     "id": "ann-1",
                     "detector": {"name": "shape", "version": "1",
                                  "kind": "code"},
                     "label": "repeated-call", "probability": None,
                     "input_sha256": "1" * 64}))
    rows.append(row(4, "conv-tool", "0.3.0", run, tid, span(3), span(2),
                    "annotation", "point", T4,
                    {"target": {"trace": tid, "run": run, "span": span(2),
                                "seq": 1},
                     "id": "ann-2", "supersedes": "ann-1",
                     "detector": {"name": "shape", "version": "1",
                                  "kind": "code"},
                     "label": "paging-not-retry", "probability": None,
                     "input_sha256": "1" * 64}))
    rows.append(row(5, "conv-tool", "0.3.0", run, tid, span(3), span(2),
                    "annotate", "end", T5, {}, took=3, outcome="ok"))
    rows.append(row(6, "conv-tool", "0.3.0", run, tid, span(2), span(1),
                    "stage", "end", T6, {}, took=5, outcome="ok"))
    rows.append(row(7, "conv-tool", "0.3.0", run, tid, span(1), None,
                    "run", "end", T7, {}, took=7, outcome="ok"))
    write_run("comp-conv-pass.jsonl", rows)
    cases.append({"run": "comp-conv-pass.jsonl",
                  "events": [conv_events, "conv-tool.json"]})

    # comp-conv-conflict: a tool file redeclaring annotation differently
    # from the conventions vocabulary is a composition error.
    tid, run = trace(), "comp-conv-conflict"
    rows = [run_start(0, "differ", "0.3.0", run, tid, span(1), T0, {
        **manifest(conv_manifest_entry("flattrace-conventions", conv_events),
                   conv_manifest_entry("differ", conv_differ_path))})]
    rows.append(row(1, "differ", "0.3.0", run, tid, span(2), span(1),
                    "annotate", "start", T1,
                    {"detector": {"name": "shape", "version": "1",
                                  "kind": "code"},
                     "questions": ["repeated-call?"], "input_sha256": "1" * 64,
                     "usage": None, "cost": None}))
    rows.append(row(2, "differ", "0.3.0", run, tid, span(2), span(1),
                    "annotation", "point", T2,
                    {"label": "repeated-call"}))
    rows.append(row(3, "differ", "0.3.0", run, tid, span(2), span(1),
                    "annotate", "end", T3, {}, took=2, outcome="ok"))
    rows.append(row(4, "differ", "0.3.0", run, tid, span(1), None,
                    "run", "end", T4, {}, took=4, outcome="ok"))
    write_run("comp-conv-conflict.jsonl", rows)
    cases.append({"run": "comp-conv-conflict.jsonl",
                  "events": [conv_events, "conv-differ.json"]})

    (COMP / "cases.json").write_text(
        json.dumps({"cases": cases}, indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(cases)} cases")


if __name__ == "__main__":
    main()
