# SPDX-License-Identifier: MIT
"""Envelope/span validation and tool-owned command coverage."""
import json
import math
import re
from datetime import datetime


def validate_event(row):
    required = {"v", "tool", "tool_version", "run", "trace", "span", "parent",
                "event", "state", "t", "data"}
    if not isinstance(row, dict) or not required <= row.keys():
        raise ValueError("missing envelope fields")
    if "seq" in row and (type(row["seq"]) is not int or row["seq"] < 0):
        raise ValueError("seq must be a nonnegative integer")
    if type(row["v"]) is not int or row["v"] != 1:
        raise ValueError("v must be 1")
    for field in ("tool", "tool_version", "run", "event", "state", "t"):
        if not isinstance(row[field], str):
            raise ValueError(f"{field} must be a string")
    for field, width in (("trace", 32), ("span", 16), ("parent", 16)):
        value = row[field]
        if field == "parent" and value is None:
            continue
        if not isinstance(value, str) or not re.fullmatch(f"[0-9a-f]{{{width}}}", value) or not int(value, 16):
            raise ValueError(f"invalid {field}")
    if row["event"] == "run" and row["state"] == "point":
        raise ValueError("run is reserved for lifetime spans")
    if row["state"] not in ("start", "end", "point") or not isinstance(row["data"], dict):
        raise ValueError("invalid state or data")
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|\+00:00)", row["t"]):
        raise ValueError("t must be RFC3339 UTC")
    datetime.fromisoformat(row["t"].replace("Z", "+00:00"))
    if row["state"] == "end":
        if "took_ms" not in row or not isinstance(row.get("outcome"), str):
            raise ValueError("end requires took_ms and outcome")
        took = row["took_ms"]
        if took is not None and (type(took) not in (int, float) or not math.isfinite(took) or took < 0):
            raise ValueError("invalid took_ms")
    elif "took_ms" in row or "outcome" in row:
        raise ValueError("took_ms and outcome belong on ends")
    json.dumps(row, allow_nan=False)
    if "error" in row:
        error = row["error"]
        if not isinstance(error, dict) or not all(isinstance(error.get(k), str) for k in ("kind", "message")):
            raise ValueError("error requires string kind and message")


def reject_constant(value):
    raise ValueError(f"invalid JSON number: {value}")


def read_trace(path):
    rows, issues = [], []
    with open(path, "rb") as stream:
        for number, line in enumerate(stream, 1):
            try:
                row = json.loads(line.decode("utf-8"), parse_constant=reject_constant)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                prefix = "incomplete tail" if not line.endswith(b"\n") else "line"
                issues.append(f"{prefix} {number}: {exc}")
                continue
            except ValueError as exc:
                issues.append(f"line {number}: {exc}")
                continue
            try:
                validate_event(row)
                rows.append(row)
            except (ValueError, TypeError) as exc:
                issues.append(f"line {number}: {exc}")
    if not rows:
        return rows, issues + ["missing run start and end"]
    first, last = rows[0], rows[-1]
    if first["event"] != "run" or first["state"] != "start":
        issues.append("first line must be run start")
    header = first["data"]
    if not (isinstance(header.get("argv"), list) and all(isinstance(v, str) for v in header["argv"])
            and isinstance(header.get("host"), str) and header.get("version") == first["tool_version"]):
        issues.append("run start requires argv, host, version")
    for key in ("task", "variant"):
        if key in header and not isinstance(header[key], str):
            issues.append(f"run {key} must be a string")
    for row in rows:
        if any(row[key] != first[key] for key in ("run", "trace", "tool", "tool_version")):
            issues.append("file contains different runs or tools")
            break
    ends = [row for row in rows if row["event"] == "run" and row["state"] == "end"]
    if not ends:
        issues.append(f"unfinished run {first['run']}: missing run end")
    elif len(ends) != 1 or ends[0] is not last or any(last[key] != first[key] for key in ("span", "parent")):
        issues.append("last line must be the matching run end")
    if sum(row["event"] == "run" and row["state"] == "start" for row in rows) != 1:
        issues.append("expected one run start")
    if any("seq" in row for row in rows):
        for expected, row in enumerate(rows):
            if row.get("seq") != expected:
                issues.append(f"seq gap or reordering: expected {expected}, got {row.get('seq')}")
    # Span identity is local to this run; external run parents need not be here.
    active, seen = {}, set()
    for row in rows:
        span, state = row["span"], row["state"]
        identity = (row["event"], row["parent"])
        if state == "start":
            if span in seen:
                issues.append(f"duplicate span start {span}")
            if row["event"] != "run" and row["parent"] not in active:
                issues.append(f"inactive parent for {span}")
            active[span] = identity
            seen.add(span)
        elif state == "end":
            if active.get(span) != identity:
                issues.append(f"unmatched end {span}")
            active.pop(span, None)
        elif span not in active or active[span][1] != row["parent"]:
            issues.append(f"point outside active span {span}")
    for span in active:
        issues.append(f"unfinished span {span}: missing end")
    return rows, issues


def check_events(rows, contract, command=None):
    """Small per-run coverage assertion; field descriptions are documentation."""
    if not rows:
        return []  # read_trace already reports missing run.
    issues = []
    if rows[0]["tool"] != contract["tool"]:
        issues.append("event list tool mismatch")
    command = rows[0]["data"].get("command") if command is None else command
    commands = contract.get("commands")
    required = contract.get("required", [])
    if commands is not None:
        if not isinstance(command, str):
            return issues + ["select a command with --command (or run data.command) for this contract"]
        if command not in commands:
            issues.append(f"undeclared command {command!r}")
        required = [*required, *commands.get(command, [])]
    present = set()
    for row in rows:
        name = row["event"]
        present.add(name)
        declaration = contract["events"].get(name)
        if declaration is None:
            issues.append(f"undeclared event {name}")
        elif declaration["kind"] != ("point" if row["state"] == "point" else "span"):
            issues.append(f"wrong event kind {name}")
    for name in required:
        if name not in present:
            issues.append(f"missing required event {name} for {command}")
    return issues


def read_events(path):
    with open(path, encoding="utf-8") as stream:
        contract = json.load(stream)
    if (not isinstance(contract, dict) or not isinstance(contract.get("tool"), str)
            or not isinstance(contract.get("events"), dict)
            or ("commands" in contract and not isinstance(contract["commands"], dict))):
        raise ValueError("event list requires tool and events, with optional commands")
    for name, event in contract["events"].items():
        if (not isinstance(event, dict) or event.get("kind") not in ("span", "point")
                or not isinstance(event.get("fields"), dict)
                or not all(isinstance(v, str) for v in event["fields"].values())):
            raise ValueError(f"invalid event declaration {name}")
    for command, names in [("required", contract.get("required", [])), *contract.get("commands", {}).items()]:
        if not isinstance(names, list) or not all(isinstance(n, str) and n in contract["events"] for n in names):
            raise ValueError(f"invalid required events for {command}")
    return contract
