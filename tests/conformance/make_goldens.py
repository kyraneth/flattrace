# SPDX-License-Identifier: MIT
"""Regenerate committed golden outputs from the pure-Python reference.

Run: python3 tests/conformance/make_goldens.py
Review the git diff before committing: goldens are the frozen contract the
Rust engine, CLI, Python and Node bindings must reproduce exactly.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import reference  # noqa: E402

CORPUS = HERE / "corpus"
GOLDEN = HERE / "golden"


def corpus_files():
    return sorted(str(p) for p in CORPUS.glob("*.jsonl*")
                  if p.name != "real")


def as_corpus_rel(value, in_file=False):
    if isinstance(value, dict):
        return {k: as_corpus_rel(v, in_file=(k == "file"))
                for k, v in value.items()}
    if isinstance(value, list):
        return [as_corpus_rel(v, in_file) for v in value]
    if in_file and isinstance(value, str) and value.endswith((".jsonl",
                                                              ".jsonl.gz")):
        try:
            return str(Path(value).relative_to(CORPUS))
        except ValueError:
            return value
    return value


def write(name, payload):
    GOLDEN.mkdir(exist_ok=True)
    path = GOLDEN / name
    path.write_text(json.dumps(as_corpus_rel(payload), indent=2, sort_keys=True)
                    + "\n")
    print(f"wrote {path}")


def write_composition():
    comp = HERE / "composition"
    cases = json.loads((comp / "cases.json").read_text())["cases"]
    out = []
    prefix = str(comp) + "/"
    for case in cases:
        events = [str(comp / name) for name in case["events"]]
        entry = dict(reference.check([str(comp / case["run"])],
                                     events, case.get("command"))[0])
        # Goldens stay portable: composition messages name files by basename.
        entry["file"] = case["run"]
        entry["events"] = list(case["events"])
        entry["issues"] = [s.replace(prefix, "") for s in entry["issues"]]
        entry["notices"] = [s.replace(prefix, "") for s in entry["notices"]]
        out.append(entry)
    write("check_composition.json", out)


def main():
    files = corpus_files()
    closedata = [str(CORPUS / "closedata" / "01.jsonl")]
    for p in closedata:
        assert Path(p).exists(), p
    parity = sorted(str(p) for p in (CORPUS / "parity").glob("*.jsonl"))
    assert parity, "parity fixtures missing"
    write("check.json", reference.check(files))
    write("check_events.json",
          reference.check([str(CORPUS / "c01-parent.jsonl")],
                          str(HERE / "agent-events.json")))
    write("spans_all.json", reference.spans(files))
    write("spans_filtered.json",
          reference.spans(files, {"event": "tool.exec",
                                  "data": {"shot": "sh001"}}))
    write("spans_agent.json",
          reference.spans(files, {"agent": "synthetic-agent"}))
    write("spans_query.json",
          reference.spans(files, {"model": "synthetic-model-b",
                                  "outcome": "ok"}))
    write("spans_session.json",
          reference.spans(files, {"session": "session-7"}))
    write("spans_errors.json",
          reference.spans(files, {"error_kind": "io"}))
    write("spans_window.json",
          reference.spans(files, {"t_from": "2026-10-20T00:00:01.000Z",
                                  "t_to": "2026-10-20T00:00:01.050Z"}))
    write("digest.json", reference.digest(files))
    write("group.json", reference.group(files, ["tool", "event"]))
    write("group_label.json", reference.group(files, ["event", "data.shot"]))
    write("tree.json", reference.tree(files, "a" * 32))
    write("compare.json",
          reference.compare([str(CORPUS / "c08-baseline.jsonl")],
                            [str(CORPUS / "c09-candidate.jsonl")]))
    write("overlap.json",
          reference.overlap(files, "loop.stall", {"event": "tool.exec"}))
    write("spans_runend.json", reference.spans(closedata))
    write("parity_digest.json", reference.digest(parity))
    write("parity_spans_model_nested.json",
          reference.spans(parity, {"model": "nested-m"}))
    write("parity_spans_model_empty.json",
          reference.spans(parity, {"model": "env-m"}))
    write_composition()


if __name__ == "__main__":
    main()
