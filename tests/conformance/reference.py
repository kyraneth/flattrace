# SPDX-License-Identifier: MIT
"""Test-only pure-Python reference for the frozen reader contract (docs/reader.md).

Not shipped: the real engine is Rust (crates/flattrace-read) with PyO3/napi
bindings. This module generates the committed golden outputs and pins the
semantics the engine must reproduce: pairing and self policy mirror
flattrace/stats.py exactly; check() delegates to flattrace.check.
"""
import gzip
import json
import math
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from flattrace.check import read_trace, read_events, check_events  # noqa: E402


def parse_t(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def get_path(data, dotted):
    current = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def count_of(data):
    count = data.get("count", 1) if isinstance(data, dict) else 1
    return count if type(count) is int and count >= 1 else 1


def nearest_rank(values):
    """Exact nearest-rank summary over known values; None when empty."""
    known = [v for v in values if v is not None]
    if not known:
        return None
    ordered = sorted(known)
    n = len(ordered)
    summary = {"n": n, "p50": None, "p95": None, "max": ordered[-1],
               "total": sum(ordered, 0)}
    for label, percentile in (("p50", 0.5), ("p95", 0.95)):
        summary[label] = ordered[math.ceil(n * percentile) - 1]
    return summary


class Frame:
    def __init__(self, event, parent, depth):
        self.event = event
        self.parent = parent
        self.depth = depth
        self.children = set()
        self.permanent_children = set()
        self.child_ms = 0.0
        self.reason = None


def pair_run(path, rows, duplicates, errors, warnings):
    """Mirror flattrace.stats._read_run pairing/self/unknown policy."""
    issues = []
    active = {}
    seen = set()
    identity = (rows[0]["trace"], rows[0]["run"]) if rows else (None, None)
    duplicate = identity in duplicates
    duplicates.add(identity)
    if not rows:
        return None, issues, True
    run = {"path": str(path), "trace": rows[0]["trace"], "run": rows[0]["run"],
           "tool": rows[0]["tool"], "tool_version": rows[0]["tool_version"],
           "status": "unfinished", "took_ms": None, "level": "unknown",
           "events": 0, "header": rows[0]["data"]}
    root = rows[0]["span"] if (rows[0]["event"] == "run"
                                and rows[0]["state"] == "start") else None
    last = None
    expected_seq, has_seq, missing_seq = 0, False, False
    unknown = {}
    unfinished = {}
    frames = {}
    points, spans = [], []

    def note(category, detail):
        issues.append((category, detail))

    for number, row in enumerate(rows, 1):
        if any(row[key] != run[key] for key in ("trace", "run", "tool", "tool_version")):
            note("mixed runs or tools", f"line {number}: file contains different runs or tools")
            continue
        run["events"] += 1
        if "seq" in row:
            has_seq = True
            if row["seq"] != expected_seq or missing_seq:
                note("seq gap or reordering", f"line {number}: seq gap or reordering")
        else:
            missing_seq = True
            if has_seq:
                note("missing seq", f"line {number}: missing seq")
        expected_seq += 1
        last = (row["event"], row["state"], row["span"])
        data = row["data"]
        severity = data.get("severity")
        count = count_of(data)
        if not duplicate:
            if "error" in row or row.get("outcome") == "error" or severity == "error":
                errors[row["event"]] = errors.get(row["event"], 0) + count
            if severity in ("warning", "warn"):
                warnings[row["event"]] = warnings.get(row["event"], 0) + count
        span, parent, name, state = (row[k] for k in ("span", "parent", "event", "state"))
        if state == "start":
            if span in seen:
                note("duplicate span start", f"line {number}: duplicate span start")
                continue
            frame = Frame(name, parent, len(active))
            if name == "run" and (root is None or span != root):
                note("unexpected run start", f"line {number}: unexpected run start")
            if (root is None or span != root) and parent not in active:
                note("inactive parent", f"line {number}: inactive parent")
            if parent in active:
                owner = active[parent]
                if owner.children:
                    owner.reason = "overlapping children"
                owner.children.add(span)
                owner.permanent_children.add(span)
            active[span] = frame
            seen.add(span)
            frames[span] = {"span": span, "parent": parent, "event": name,
                            "start_t": row["t"], "start_seq": row["seq"]
                            if "seq" in row else None,
                            "data": data, "depth": frame.depth}
        elif state == "end":
            frame = active.get(span)
            if frame is None or (frame.event, frame.parent) != (name, parent):
                note("unmatched end", f"line {number}: unmatched end")
                continue
            del active[span]
            total = row["took_ms"]
            reason = frame.reason
            if frame.children:
                reason = "child outlives parent"
                ancestor = parent
                while ancestor in active:
                    active[ancestor].reason = "async descendant"
                    ancestor = active[ancestor].parent
            if total is None:
                reason = "unknown duration"
            elif frame.child_ms > total:
                reason = "children exceed parent duration"
            self_ms = None if reason else total - frame.child_ms
            if reason:
                unknown[(name, reason)] = unknown.get((name, reason), 0) + 1
            entry = dict(frames[span])
            entry.update({"trace": row["trace"], "run": row["run"],
                          "tool": row["tool"],
                          "tool_version": row["tool_version"],
                          "end_t": row["t"],
                          "end_seq": row["seq"] if "seq" in row else None,
                          "took_ms": total, "outcome": row["outcome"],
                          "unfinished": False, "end_data": row["data"],
                          "error": row.get("error"),
                          "self_ms": self_ms,
                          "self_unknown": reason,
                          "children": sorted(frame.permanent_children)})
            spans.append(entry)
            if parent in active:
                owner = active[parent]
                owner.children.discard(span)
                if total is None:
                    owner.reason = "unknown child duration"
                else:
                    owner.child_ms += total
            if root is not None and span == root:
                run["status"], run["took_ms"] = row["outcome"], total
        else:
            if span not in active or active[span].parent != parent:
                note("point outside active span",
                     f"line {number}: point outside active span")
                continue
            points.append({"span": span, "event": name, "t": row["t"],
                           "seq": row["seq"] if "seq" in row else None,
                           "data": data,
                           "error": row.get("error") if "error" in row else None})
    if rows[0]["event"] != "run" or rows[0]["state"] != "start":
        note("first line must be run start", "first valid line must be run start")
    else:
        header = rows[0]["data"]
        if not (isinstance(header.get("argv"), list)
                and all(isinstance(v, str) for v in header["argv"])
                and isinstance(header.get("host"), str)
                and header.get("version") == rows[0]["tool_version"]):
            note("run start requires argv, host, version",
                 "run start requires argv, host, version")
        level = header.get("level")
        if type(level) is int and level >= 0:
            run["level"] = str(level)
    if root is None or last != ("run", "end", root):
        note("missing final matching run end", "missing final matching run end")
    for span, frame in active.items():
        unfinished[frames[span]["event"]] = unfinished.get(frames[span]["event"], 0) + 1
        entry = dict(frames[span])
        entry.update({"trace": run["trace"], "run": run["run"],
                      "tool": run["tool"],
                      "tool_version": run["tool_version"],
                      "end_t": None, "end_seq": None, "took_ms": None,
                      "outcome": None, "unfinished": True, "end_data": None,
                      "error": None, "self_ms": None,
                      "self_unknown": "unfinished",
                      "children": sorted(frame.permanent_children)})
        spans.append(entry)
    if unfinished:
        note("unfinished spans", "unfinished spans")
    if issues:
        for entry in spans:
            if entry["self_ms"] is not None:
                entry["self_ms"] = None
                entry["self_unknown"] = "incomplete or invalid run"
                key = (entry["event"], "incomplete or invalid run")
                unknown[key] = unknown.get(key, 0) + 1
    by_span = {entry["span"]: entry for entry in spans}
    for point in points:
        by_span[point["span"]].setdefault("points", []).append(
            {k: point[k] for k in ("event", "t", "seq", "data", "error")})
    for entry in spans:
        entry.setdefault("points", []).sort(
            key=lambda p: (p["seq"] is None, p["seq"] or 0))
    run["spans"] = sorted(spans, key=lambda e: (e["start_seq"] is None,
                                                e["start_seq"] or 0))
    env = rows[0]["data"].get("env")
    run["agent"] = env.get("agent.name") if isinstance(env, dict) else None
    run["unknown"] = {f"{name}::{reason}": count
                      for (name, reason), count in sorted(unknown.items())}
    run["unfinished"] = dict(sorted(unfinished.items()))
    run["issues"] = [detail for _, detail in issues]
    run["issue_counts"] = {}
    for category, _ in issues:
        run["issue_counts"][category] = run["issue_counts"].get(category, 0) + 1
    run["duplicate"] = duplicate
    return run, [detail for _, detail in issues], False


def iter_input_lines(path):
    """Raw text lines, mirroring read_trace line splitting (unused by pairing)."""
    text = Path(path).read_bytes()
    if str(path).endswith(".gz"):
        text = gzip.decompress(text)
    return text.splitlines(keepends=True)


def load_corpus(paths):
    """Read, validate, dedup (first path wins) and pair every file."""
    files, runs, duplicates, invalid = [], [], set(), []
    errors, warnings = {}, {}
    seen_identities = set()
    for path in paths:
        rows, file_issues, _ = read_trace(str(path))
        record = {"file": str(path), "rows": len(rows), "issues": file_issues}
        files.append(record)
        if not rows:
            invalid.append(record)
            continue
        identity = (rows[0]["trace"], rows[0]["run"])
        if identity in seen_identities:
            record["duplicate_of"] = True
        seen_identities.add(identity)
        run, _, _ = pair_run(str(path), rows, duplicates, errors, warnings)
        if record.get("duplicate_of"):
            # Duplicates are still fully validated; only samples are excluded.
            # pair_run already skipped sample/error recording via the flag.
            run["duplicate_excluded"] = True
        runs.append(run)
        if file_issues:
            record["invalid"] = True
    return {"files": files, "runs": runs, "invalid": invalid,
            "errors": errors, "warnings": warnings}


def _primary_runs(corpus):
    seen = set()
    out = []
    for run in corpus["runs"]:
        identity = (run["trace"], run["run"])
        if identity in seen:
            continue
        seen.add(identity)
        out.append(run)
    return out


def _as_list(value):
    return value if isinstance(value, list) else [value]


def _match(span, run, filt):
    for key in ("tool", "event", "trace", "run"):
        if key in filt and span[key] not in _as_list(filt[key]):
            return False
    if "outcome" in filt:
        wanted = set(_as_list(filt["outcome"]))
        actual = span["outcome"] if not span["unfinished"] else "unfinished"
        if actual not in wanted:
            return False
    if filt.get("unfinished") is True and not span["unfinished"]:
        return False
    if filt.get("unfinished") is False and span["unfinished"]:
        return False
    if "agent" in filt and run.get("agent") != filt["agent"]:
        return False
    if "model" in filt and _model_of(span, run) != filt["model"]:
        return False
    if "session" in filt and _session_of(span, run) != filt["session"]:
        return False
    if "error_kind" in filt:
        kinds = {r["kind"] for r in error_records_of(span)}
        if filt["error_kind"] not in kinds:
            return False
    if "t_from" in filt and parse_t(span["start_t"]) < parse_t(filt["t_from"]):
        return False
    if "t_to" in filt and parse_t(span["start_t"]) > parse_t(filt["t_to"]):
        return False
    for dotted, want in (filt.get("data") or {}).items():
        if get_path(span["data"], dotted) != want:
            return False
    return True


def _model_of(span, run):
    header = run.get("header") or {}
    env = header.get("env") if isinstance(header.get("env"), dict) else {}
    return (span["data"].get("gen_ai.request.model")
            or span["data"].get("model") or env.get("gen_ai.request.model"))


def span_row(span, path):
    return {"file": str(path), "trace": span["trace"],
            "run": span["run"], "span": span["span"],
            "parent": span["parent"], "tool": span["tool"],
            "tool_version": span["tool_version"], "event": span["event"],
            "start_t": span["start_t"], "start_seq": span["start_seq"],
            "end_t": span["end_t"], "end_seq": span["end_seq"],
            "took_ms": span["took_ms"], "outcome": span["outcome"],
            "unfinished": span["unfinished"], "depth": span["depth"],
            "children": span["children"], "data": span["data"],
            "end_data": span["end_data"], "error": span["error"],
            "self_ms": span["self_ms"],
            "self_unknown": span["self_unknown"],
            "points": span["points"]}


def _session_of(span, run):
    header = run.get("header") or {}
    return (span["data"].get("session_id") or span["data"].get("session")
            or header.get("session_id") or header.get("session"))


def _key_value(span, run, name):
    if name == "outcome":
        return span["outcome"] if not span["unfinished"] else "unfinished"
    if name == "agent":
        return run.get("agent")
    if name == "model":
        return _model_of(span, run)
    if name == "session":
        return _session_of(span, run)
    if name in ("tool", "event", "trace", "run"):
        return span[name]
    if name.startswith("data."):
        return get_path(span["data"], name[5:])
    if name.startswith("error."):
        kinds = {r.get(name[6:]) for r in error_records_of(span)}
        kinds.discard(None)
        if not kinds:
            return None
        return sorted(kinds, key=str)[0] if len(kinds) == 1 else sorted(
            kinds, key=str)
    raise ValueError(f"unknown group key {name!r}")


def pointer_of(span, path=None):
    return {"file": span["file"] if path is None else str(path),
            "start_seq": span["start_seq"], "span": span["span"]}


def error_summary(records):
    return {"records": len(records),
            "occurrences": sum(r["count"] for r in records)}


def coverage_of(corpus):
    runs = _primary_runs(corpus)
    complete = sum(1 for r in runs if r["status"] != "unfinished")
    spans = [s for r in runs for s in r["spans"]]
    unfinished = sum(1 for s in spans if s["unfinished"])
    points = sum(len(s["points"]) for s in spans)
    issues = sum(len(r["issues"]) for r in runs)
    duplicates = sum(1 for f in corpus["files"] if f.get("duplicate_of"))
    duplicates_with_issues = sum(
        1 for r in corpus["runs"]
        if r.get("duplicate_excluded") and r["issues"])
    invalid = len(corpus["invalid"])
    coverage = {"files": len(corpus["files"]), "runs": len(runs),
                "complete_runs": complete,
                "unfinished_runs": len(runs) - complete,
                "invalid_files": invalid,
                "duplicates_excluded": duplicates,
                "duplicates_with_issues": duplicates_with_issues,
                "spans": len(spans), "spans_unfinished": unfinished,
                "points": points, "issues": issues}
    coverage["statement"] = (
        f"{coverage['files']} files, {coverage['runs']} runs "
        f"({complete} complete, {len(runs) - complete} unfinished, "
        f"{invalid} invalid), {duplicates} duplicate excluded, "
        f"{len(spans)} spans paired ({unfinished} unfinished), "
        f"{points} points, {issues} issues")
    return coverage


def error_records_of(span):
    """One error record per end/point carrying error or outcome error."""
    records = []
    if span.get("error") is not None or span.get("outcome") == "error":
        error = span.get("error") or {}
        records.append({"kind": error.get("kind"),
                        "message": error.get("message"),
                        "where": get_path(span.get("end_data") or {}, "where"),
                        "count": count_of(span.get("end_data") or {})})
    for point in span.get("points", []):
        if point.get("error") is not None:
            records.append({"kind": point["error"].get("kind"),
                            "message": point["error"].get("message"),
                            "where": get_path(point["data"], "where"),
                            "count": count_of(point["data"])})
    return records


def check(paths, events_paths=None, command=None):
    if events_paths is None:
        events_paths = []
    elif isinstance(events_paths, (str, Path)):
        events_paths = [events_paths]
    contracts = [read_events(path) for path in events_paths]
    result = []
    for path in paths:
        rows, file_issues, file_notices = read_trace(str(path))
        issues = list(file_issues)
        notices = list(file_notices)
        if contracts:
            extra, extra_notices = check_events(rows, contracts, command)
            issues.extend(extra)
            notices.extend(extra_notices)
        result.append({"file": str(path), "rows": len(rows),
                       "issues": issues, "notices": notices})
    return result


def select_spans(corpus, filt):
    filt = filt or {}
    out = []
    for run in _primary_runs(corpus):
        for span in run["spans"]:
            if _match(span, run, filt):
                out.append((run, span))
    return out


def spans(paths, filt=None):
    corpus = load_corpus(paths)
    return [span_row(span, run["path"])
            for run, span in select_spans(corpus, filt)]


def digest(paths, scope=None):
    corpus = load_corpus(paths)
    pairs = select_spans(corpus, scope)
    table = {}
    for run, span in pairs:
        key = (span["tool"], span["event"])
        bucket = table.setdefault(key, {"rows": [], "errors": [],
                                        "unfinished": 0, "self_unknown": {}})
        bucket["rows"].append(span)
        if span["unfinished"]:
            bucket["unfinished"] += 1
        if span["self_unknown"]:
            reason = span["self_unknown"]
            bucket["self_unknown"][reason] = \
                bucket["self_unknown"].get(reason, 0) + 1
        bucket["errors"].extend(error_records_of(span))
    groups = []
    for (tool, event) in sorted(table):
        bucket = table[(tool, event)]
        rows = bucket["rows"]
        slowest = None
        for run, span in pairs:
            if (span["tool"], span["event"]) != (tool, event):
                continue
            if span["took_ms"] is not None and (slowest is None or
                    span["took_ms"] > slowest[1]["took_ms"]):
                slowest = (run, span)
        groups.append({"tool": tool, "event": event, "n": len(rows),
                       "took": nearest_rank([s["took_ms"] for s in rows]),
                       "self": nearest_rank([s["self_ms"] for s in rows]),
                       "self_unknown": dict(sorted(
                           bucket["self_unknown"].items())),
                       "errors": error_summary(bucket["errors"]),
                       "unfinished": bucket["unfinished"],
                       "slowest": (pointer_of(slowest[1], slowest[0]["path"])
                                   if slowest else None)})
    census = {}
    for run, span in pairs:
        for record in error_records_of(span):
            key = (span["tool"], span["event"], record["kind"],
                   record["where"])
            slot = census.setdefault(key, {"records": 0, "occurrences": 0,
                                           "example": None, "message": None})
            slot["records"] += 1
            slot["occurrences"] += record["count"]
            if slot["example"] is None:
                slot["example"] = pointer_of(span, run["path"])
                slot["message"] = record["message"]
    errors = [{"tool": tool, "event": event, "kind": kind,
               "where": where, "records": slot["records"],
               "occurrences": slot["occurrences"],
               "message": slot["message"], "example": slot["example"]}
              for (tool, event, kind, where), slot in sorted(
                  census.items(), key=lambda kv: str(kv[0]))]
    unfinished = [pointer_of(span, run["path"])
                  for run, span in pairs if span["unfinished"]]
    return {"coverage": coverage_of(corpus), "table": groups,
            "errors": errors, "unfinished": unfinished}


def group(paths, by, metrics=None, filt=None):
    corpus = load_corpus(paths)
    pairs = select_spans(corpus, filt)
    buckets = {}
    for run, span in pairs:
        key = tuple(_key_value(span, run, name) for name in by)
        buckets.setdefault(key, []).append((run, span))
    names = ["n", "took", "self", "errors", "unfinished", "points"]
    wanted = names if metrics is None else [m for m in metrics if m in names]
    groups = []
    for key in sorted(buckets, key=lambda k: [str(v) for v in k]):
        members = buckets[key]
        entry = {"key": {name: value for name, value in zip(by, key)}}
        if "n" in wanted:
            entry["n"] = len(members)
        if "took" in wanted:
            entry["took"] = nearest_rank([s["took_ms"] for _, s in members])
        if "self" in wanted:
            entry["self"] = nearest_rank([s["self_ms"] for _, s in members])
        if "errors" in wanted:
            records = [r for _, s in members for r in error_records_of(s)]
            entry["errors"] = error_summary(records)
        if "unfinished" in wanted:
            entry["unfinished"] = sum(1 for _, s in members
                                        if s["unfinished"])
        if "points" in wanted:
            entry["points"] = sum(len(s["points"]) for _, s in members)
        groups.append(entry)
    return {"coverage": coverage_of(corpus), "by": list(by),
            "groups": groups}


def tree(paths, trace):
    corpus = load_corpus(paths)
    runs = [r for r in _primary_runs(corpus) if r["trace"] == trace]
    nodes = {}
    for run in runs:
        root = next((s for s in run["spans"] if s["event"] == "run"), None)
        parent_span = root["parent"] if root else None
        nodes[run["run"]] = {
            "run": run["run"], "file": run["path"],
            "tool": run["tool"], "status": run["status"],
            "took_ms": run["took_ms"], "outcome": run["status"],
            "parent_span": parent_span,
            "parent_run": None, "parent_outside_corpus": False,
            "spans": len(run["spans"]),
            "unfinished_spans": sum(1 for s in run["spans"]
                                    if s["unfinished"]),
            "points": sum(len(s["points"]) for s in run["spans"]),
            "failed": run["status"] == "error", "children": []}
    for run in runs:
        node = nodes[run["run"]]
        parent_span = node["parent_span"]
        if parent_span is None:
            continue
        owner = next((r for r in runs if any(
            s["span"] == parent_span for s in r["spans"])), None)
        if owner is not None:
            node["parent_run"] = owner["run"]
            nodes[owner["run"]]["children"].append(node)
        else:
            node["parent_outside_corpus"] = True
    for node in nodes.values():
        node["children"].sort(key=lambda c: (
            c["took_ms"] is None, -(c["took_ms"] or 0), c["run"]))
    roots = [n for n in nodes.values() if n["parent_run"] is None]
    roots.sort(key=lambda c: (c["took_ms"] is None, -(c["took_ms"] or 0),
                               c["run"]))
    failed = sorted(n["run"] for n in nodes.values() if n["failed"])
    slowest = None
    for node in nodes.values():
        if node["took_ms"] is not None and (slowest is None or
                nodes[slowest]["took_ms"] < node["took_ms"]):
            slowest = node["run"]
    return {"coverage": coverage_of(corpus), "trace": trace,
            "roots": roots, "failed": failed, "slowest": slowest}


def compare(a_paths, b_paths, filt=None):
    def side(paths):
        corpus = load_corpus(paths)
        pairs = select_spans(corpus, filt)
        table = {}
        for run, span in pairs:
            table.setdefault((span["tool"], span["event"]), []).append(span)
        events = []
        for (tool, event) in sorted(table):
            members = table[(tool, event)]
            records = [r for s in members for r in error_records_of(s)]
            events.append({"tool": tool, "event": event,
                           "summary": event_summary(members, records)})
        return {"coverage": coverage_of(corpus),
                "runs": [{"run": r["run"], "tool": r["tool"],
                            "outcome": r["status"],
                            "took_ms": r["took_ms"]}
                           for r in _primary_runs(corpus)],
                "events": events}
    aside, bside = side(a_paths), side(b_paths)
    a_map = {(e["tool"], e["event"]): e["summary"]
             for e in aside["events"]}
    b_map = {(e["tool"], e["event"]): e["summary"]
             for e in bside["events"]}
    events = []
    for key in sorted(set(a_map) | set(b_map)):
        tool, event = key
        a, b = a_map.get(key), b_map.get(key)
        entry = {"tool": tool, "event": event, "a": a, "b": b,
                 "delta_n": None, "delta_p50": None, "delta_total": None}
        if a is not None and b is not None:
            entry["delta_n"] = b["n"] - a["n"]
            if a["took"] is not None and b["took"] is not None:
                entry["delta_p50"] = _subtract(b["took"]["p50"],
                                                 a["took"]["p50"])
                entry["delta_total"] = _subtract(b["took"]["total"],
                                                   a["took"]["total"])
        events.append(entry)
    return {"a": aside, "b": bside, "events": events}


def event_summary(members, records):
    return {"n": len(members),
            "took": nearest_rank([s["took_ms"] for s in members]),
            "errors": error_summary(records),
            "unfinished": sum(1 for s in members if s["unfinished"])}


def _subtract(left, right):
    if left is None or right is None:
        return None
    return left - right


def overlap(paths, window_event, span_filter=None):
    corpus = load_corpus(paths)
    pairs = select_spans(corpus, None)
    windows = [(run, span) for run, span in pairs
               if span["event"] == window_event and span["event"] != "run"]
    members = [(run, span) for run, span in
               select_spans(corpus, span_filter)
               if span["event"] != "run" and span["event"] != window_event]
    bounded, unbounded = [], []
    for run, span in windows:
        record = {"window": pointer_of(span, run["path"]),
                  "start_t": span["start_t"], "end_t": span["end_t"]}
        if span["end_t"] is None:
            unbounded.append(record)
            continue
        start, end = parse_t(span["start_t"]), parse_t(span["end_t"])
        hits = []
        for other_run, other in members:
            other_start = parse_t(other["start_t"])
            other_end = (parse_t(other["end_t"]) if other["end_t"]
                         else datetime.max.replace(tzinfo=start.tzinfo))
            if other_start <= end and other_end >= start:
                hits.append({**pointer_of(other, other_run["path"]),
                             "event": other["event"],
                             "took_ms": other["took_ms"],
                             "outcome": other["outcome"],
                             "unfinished": other["unfinished"]})
        hits.sort(key=lambda h: (h["start_seq"] is None,
                                 h["start_seq"] or 0, h["span"]))
        record["overlapping"] = hits
        bounded.append(record)
    order = lambda w: (w["start_t"], w["window"]["start_seq"] or 0)
    bounded.sort(key=order)
    unbounded.sort(key=order)
    return {"coverage": coverage_of(corpus), "window_event": window_event,
            "windows": bounded, "unbounded_windows": unbounded}
