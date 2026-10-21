# SPDX-License-Identifier: MIT
"""Streaming corpus analysis; exact quantiles spill to a disposable SQLite file."""
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import zlib

from .check import _lines, reject_constant, validate_event


def trace_files(inputs):
    """Recursive discovery, deterministic first-copy wins; never follow dir links."""
    seen = set()
    for value in inputs:
        path = Path(value)
        if path.is_dir():
            def fail(error):
                raise error
            def walk():
                for root, dirs, names in os.walk(path, onerror=fail):
                    dirs.sort()
                    for name in sorted(names):
                        if name.endswith((".jsonl", ".jsonl.gz")):
                            yield Path(root) / name
            paths = walk()
        else:
            paths = iter([path])
        for item in paths:
            key = item.resolve()
            if key not in seen:
                seen.add(key)
                yield item


@dataclass
class Frame:
    event: str
    parent: str | None
    children: set = field(default_factory=set)
    child_ms: float = 0.0
    reason: str | None = None


def _counts(counter):
    return dict(sorted(counter.items()))


class IssueLog:
    """Count fixed diagnostic categories, retaining only the first source detail."""
    def __init__(self):
        self.counts = Counter()
        self.first = {}

    def add(self, category, detail=None):
        self.counts[category] += 1
        self.first.setdefault(category, detail or category)

    def append(self, message):
        # _lines supplies archive diagnostics, with variable exception details.
        self.add(message.partition(":")[0], message)

    def __bool__(self):
        return bool(self.counts)

    def render(self):
        return [self.first[k] + (f" (occurrences: {n})" if n > 1 else "")
                for k, n in self.counts.items()]


def _distribution(db, column, per_run):
    # SQL owns the sample store/sort; no Python list proportional to event count.
    keys = "run, name" if per_run else "name"
    result = {}
    for row in db.execute(f"SELECT {keys}, count(*), sum({column}), max({column}) "
                          f"FROM spans WHERE {column} IS NOT NULL GROUP BY {keys}"):
        *group, n, total, maximum = row
        result[tuple(group)] = dict(n=n, p50=None, p95=None, max=maximum, total=total)
    previous, rank = None, 0
    for row in db.execute(f"SELECT {keys}, {column} FROM spans WHERE {column} IS NOT NULL "
                          f"ORDER BY {keys}, {column}"):
        *group, value = row
        key = tuple(group)
        rank = rank + 1 if key == previous else 1
        previous = key
        summary = result[key]
        for label, percentile in (("p50", .5), ("p95", .95)):
            if rank == math.ceil(summary["n"] * percentile):
                summary[label] = value
    return result


def _input_lines(path, issues):
    try:
        yield from _lines(path, issues)
    except zlib.error as exc:
        issues.append(f"corrupt gzip archive: {exc}")


def _read_run(path, index, db, identities, duplicates, errors, warnings):
    issues, active = IssueLog(), {}
    db.execute("DELETE FROM seen")
    run = None
    duplicate = False
    root = None
    last = None
    expected_seq = 0
    has_seq = False
    missing_seq = False
    unknown = Counter()
    unfinished = Counter()
    for number, line in enumerate(_input_lines(path, issues), 1):
        try:
            row = json.loads(line.decode("utf-8"), parse_constant=reject_constant)
            validate_event(row)
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            prefix = "line" if line.endswith(b"\n") else "incomplete tail"
            issues.add(prefix, f"{prefix} {number}: {exc}")
            continue
        if run is None:
            identity = (row["trace"], row["run"])
            duplicate = identity in identities
            identities.add(identity)
            run = dict(path=str(path), trace=row["trace"], run=row["run"], tool=row["tool"],
                       tool_version=row["tool_version"], status="unfinished", took_ms=None,
                       level="unknown", events=0)
            if row["event"] != "run" or row["state"] != "start":
                issues.append("first valid line must be run start")
            else:
                root = row["span"]
                header = row["data"]
                if not (isinstance(header.get("argv"), list)
                        and all(isinstance(v, str) for v in header["argv"])
                        and isinstance(header.get("host"), str)
                        and header.get("version") == row["tool_version"]):
                    issues.append("run start requires argv, host, version")
                level = header.get("level")
                if type(level) is int and level >= 0:
                    run["level"] = str(level)
        if any(row[key] != run[key] for key in ("trace", "run", "tool", "tool_version")):
            issues.add("mixed runs or tools", f"line {number}: file contains different runs or tools")
            continue
        run["events"] += 1
        if "seq" in row:
            has_seq = True
            if row["seq"] != expected_seq or missing_seq:
                issues.add("seq gap or reordering", f"line {number}: seq gap or reordering")
        else:
            missing_seq = True
            if has_seq:
                issues.add("missing seq", f"line {number}: missing seq")
        expected_seq += 1
        last = (row["event"], row["state"], row["span"])
        data = row["data"]
        severity = data.get("severity")
        count = data.get("count", 1)
        count = count if type(count) is int and count >= 1 else 1
        if not duplicate:
            if "error" in row or row.get("outcome") == "error" or severity == "error":
                errors[row["event"]] += count
            if severity in ("warning", "warn"):
                warnings[row["event"]] += count
        span, parent, name, state = (row[k] for k in ("span", "parent", "event", "state"))
        if state == "start":
            try:
                db.execute("INSERT INTO seen VALUES (?)", (span,))
            except sqlite3.IntegrityError:
                issues.add("duplicate span start", f"line {number}: duplicate span start")
                continue
            frame = Frame(name, parent)
            if name == "run" and span != root:
                issues.add("unexpected run start", f"line {number}: unexpected run start")
            if span != root and parent not in active:
                issues.add("inactive parent", f"line {number}: inactive parent")
            if parent in active:
                owner = active[parent]
                if owner.children:
                    owner.reason = "overlapping children"
                owner.children.add(span)
            active[span] = frame
        elif state == "end":
            frame = active.get(span)
            if frame is None or (frame.event, frame.parent) != (name, parent):
                issues.add("unmatched end", f"line {number}: unmatched end")
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
                unknown[(name, reason)] += 1
            if not duplicate:
                db.execute("INSERT INTO spans VALUES (?, ?, ?, ?)", (index, name, total, self_ms))
            if parent in active:
                owner = active[parent]
                owner.children.discard(span)
                if total is None:
                    owner.reason = "unknown child duration"
                else:
                    owner.child_ms += total
            if span == root:
                run["status"], run["took_ms"] = row["outcome"], total
        elif span not in active or active[span].parent != parent:
            issues.add("point outside active span", f"line {number}: point outside active span")
    if run is None:
        issues.append("missing run start and end")
        return dict(path=str(path), trace=None, run=None, tool=None, status="invalid",
                    took_ms=None, level="unknown", events=0,
                    issues=issues.render(), issue_counts=_counts(issues.counts),
                    unfinished_spans={}, self_unknown={})
    if root is None or last != ("run", "end", root):
        issues.append("missing final matching run end")
    for frame in active.values():
        unfinished[frame.event] += 1
    if unfinished:
        issues.append("unfinished spans")
    run["issues"] = issues.render()
    run["issue_counts"] = _counts(issues.counts)
    if duplicate:
        duplicates.append({key: run[key] for key in ("path", "trace", "run", "status", "issues", "issue_counts")})
        return None
    if issues:
        # Missing/corrupt/reordered records can conceal a child or overlap.
        for name, count in db.execute("SELECT name, count(*) FROM spans WHERE run=? AND self IS NOT NULL GROUP BY name", (index,)):
            unknown[(name, "incomplete or invalid run")] += count
        db.execute("UPDATE spans SET self=NULL WHERE run=?", (index,))
    run["unfinished_spans"] = _counts(unfinished)
    run["self_unknown"] = {}
    for (name, reason), count in sorted(unknown.items()):
        run["self_unknown"].setdefault(name, {})[reason] = count
    return run


def analyse(inputs):
    """Read each line once, retaining active spans and disk-backed exact samples."""
    with tempfile.TemporaryDirectory(prefix="flattrace-stats-") as directory:
        with closing(sqlite3.connect(str(Path(directory) / "samples.sqlite"))) as db:
            db.execute("CREATE TABLE spans (run INTEGER, name TEXT, total REAL, self REAL)")
            db.execute("CREATE INDEX spans_run ON spans(run)")
            db.execute("CREATE TABLE seen (span TEXT PRIMARY KEY)")
            runs, duplicates, identities, invalid_files = [], [], set(), []
            errors, warnings = Counter(), Counter()
            files = 0
            for path in trace_files(inputs):
                files += 1
                run = _read_run(path, len(runs), db, identities, duplicates, errors, warnings)
                if run is not None:
                    if run["run"] is None:
                        invalid_files.append(run)
                    else:
                        runs.append(run)
            if not files:
                raise ValueError("no .jsonl or .jsonl.gz trace files found")
            db.commit()
            overall_total = _distribution(db, "total", False)
            overall_self = _distribution(db, "self", False)
            local_total = _distribution(db, "total", True)
            local_self = _distribution(db, "self", True)
            totals_by_run, selves_by_run = {}, {}
            for (index, name), summary in local_total.items():
                totals_by_run.setdefault(index, {})[name] = summary
            for (index, name), summary in local_self.items():
                selves_by_run.setdefault(index, {})[name] = summary
            for index, run in enumerate(runs):
                totals = totals_by_run.get(index, {})
                selves = selves_by_run.get(index, {})
                names = set(totals)
                names.update(run["self_unknown"])
                names.update(run["unfinished_spans"])
                run["spans"] = {name: dict(total_ms=totals.get(name),
                                           self_ms=selves.get(name)) for name in sorted(names)}
                # All names, not an arbitrary top-N. Inclusive totals are not additive.
                run["dominant_spans"] = sorted(
                    (name for name in names if name != "run"),
                    key=lambda name: -(totals.get(name, {}).get("total", 0)))
            names = set(name for name, in overall_total)
            for run in runs:
                names.update(run["spans"])
            spans = {name: dict(total_ms=overall_total.get((name,)), self_ms=overall_self.get((name,)))
                     for name in sorted(names)}
    return dict(files=files, run_count=len(runs), statuses=_counts(Counter(r["status"] for r in runs)),
                levels=_counts(Counter(r["level"] for r in runs)), duplicates=duplicates, invalid_files=invalid_files,
                spans=spans, errors_by_name=_counts(errors), warnings_by_name=_counts(warnings),
                slowest_runs=sorted(range(len(runs)), key=lambda i: (
                    runs[i]["took_ms"] is None, -(runs[i]["took_ms"] or 0))), runs=runs)


def _summary(value):
    if value is None:
        return "n=0 p50=? p95=? max=? total=?"
    return " ".join(f"{key}={value[key]:g}" for key in ("n", "p50", "p95", "max", "total"))


def format_text(report):
    lines = [f"Runs: {report['run_count']}  files: {report['files']}  duplicates excluded: {len(report['duplicates'])}",
             "Status: " + json.dumps(report["statuses"], sort_keys=True),
             "Levels (runs): " + json.dumps(report["levels"], sort_keys=True),
             "Span durations across runs (ms; inclusive totals are NOT additive):"]
    for name, summary in report["spans"].items():
        lines += [f"  {name}: {_summary(summary['total_ms'])}",
                  f"    self: {_summary(summary['self_ms'])}"]
    lines += ["Errors by name (explicit error/outcome/severity; coalesced count): " + json.dumps(report["errors_by_name"], sort_keys=True),
              "Warnings by name (explicit data.severity=warn/warning only; otherwise unknown): " + json.dumps(report["warnings_by_name"], sort_keys=True),
              "Slowest runs, with dominant spans (inclusive ms; unknown durations last):"]
    for index in report["slowest_runs"]:
        run = report["runs"][index]
        duration = "?" if run["took_ms"] is None else f"{run['took_ms']:g}"
        lines.append(f"  {run['tool']} {run['run']} {run['status']} {duration} ms ({run['path']})")
        for name in [*run["dominant_spans"], *(["run"] if "run" in run["spans"] else [])]:
            span = run["spans"][name]
            lines += [f"    {name}: {_summary(span['total_ms'])}",
                      f"      self: {_summary(span['self_ms'])}"]
        if run["self_unknown"]:
            lines.append("    Unknown self: " + json.dumps(run["self_unknown"], sort_keys=True))
        if run["unfinished_spans"]:
            lines.append("    Unfinished: " + json.dumps(run["unfinished_spans"], sort_keys=True))
        lines.extend(f"    Issue: {issue}" for issue in run["issues"])
    for duplicate in report["duplicates"]:
        lines.extend(f"Duplicate {duplicate['path']}: {issue}" for issue in duplicate["issues"])
    for invalid in report["invalid_files"]:
        lines.append(f"Invalid file: {invalid['path']}: {'; '.join(invalid['issues'])}")
    lines.append("Self = parent residence minus sequential, contained LOCAL direct-child residence; not CPU time. Cross-process children are not subtracted. Overlap/async/unknown => unknown, never wall-time subtraction.")
    return "\n".join(lines)
