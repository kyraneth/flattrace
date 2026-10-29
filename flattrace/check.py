# SPDX-License-Identifier: MIT
"""Envelope/span validation and tool-owned command coverage."""
import gzip
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path

from .sanitize import LONE_SURROGATE


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


from .sanitize import LONE_SURROGATE


def _has_surrogate(value):
    """True when any string anywhere in the parsed row holds a lone surrogate."""
    if isinstance(value, str):
        return bool(LONE_SURROGATE.search(value))
    if isinstance(value, list):
        return any(_has_surrogate(item) for item in value)
    if isinstance(value, dict):
        return any(_has_surrogate(key) or _has_surrogate(item)
                   for key, item in value.items())
    return False


def _lines(path, issues):
    """Yield raw lines from plain JSONL or a gzip-published <name>.jsonl.gz."""
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rb") as stream:
        try:
            yield from stream
        except (EOFError, OSError) as exc:  # truncated or corrupt archive
            issues.append(f"incomplete gzip archive: {exc}")


def read_trace(path):
    """Read one trace file: validated rows plus exact check.py issue strings.

    Returns (rows, issues, notices). Notices are non-failing warnings, such
    as legacy lone-surrogate escapes that writers since 0.3.0 replace.
    """
    rows, issues, notices = [], [], []
    for number, line in enumerate(_lines(path, issues), 1):
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
            if _has_surrogate(row):
                notices.append(f"line {number}: lone surrogate escape")
        except (ValueError, TypeError) as exc:
            issues.append(f"line {number}: {exc}")
    if not rows:
        return rows, issues + ["missing run start and end"], notices
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
    return rows, issues, notices


def _canonical(value):
    """Parsed-JSON equality form: object key order ignored, array order and
    descriptions counted."""
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _display(contract):
    return contract.get("path", contract.get("tool", "<events>"))


def _valid_manifest_entry(entry):
    return (isinstance(entry, dict) and isinstance(entry.get("tool"), str)
            and isinstance(entry.get("sha256"), str))


def _declaration_level(declaration):
    level = declaration.get("level", 1)
    if not isinstance(level, int) or isinstance(level, bool) or level < 1:
        return 1
    return level


def check_events(rows, contracts, command=None):
    """Per-run coverage over the union of the supplied vocabulary files.

    `contracts` is a list of read_events() results (a single contract dict is
    accepted for convenience). Returns (issues, notices). See
    docs/reference.md for the composition truth table.
    """
    if isinstance(contracts, dict):
        contracts = [contracts]
    issues, notices = [], []
    if not rows:
        return issues, notices  # read_trace already reports missing run.
    # Byte-identical files are one vocabulary; dedupe before the owner check.
    unique = []
    seen_sha = set()
    for contract in contracts:
        sha = contract.get("sha256")
        if sha is not None:
            if sha in seen_sha:
                continue
            seen_sha.add(sha)
        unique.append(contract)
    manifest = None
    has_manifest = False
    data = rows[0].get("data")
    if isinstance(data, dict) and "vocabularies" in data:
        has_manifest = True
        manifest = data.get("vocabularies")
    if not has_manifest:
        # Legacy run: compose the given files; lack of fingerprints is
        # unverified provenance, not a failure and not invented provenance.
        effective = list(unique)
        if contracts:
            notices.append("unverified provenance: run has no data.vocabularies")
        single = len(unique) == 1
    else:
        single = False
        if (not isinstance(manifest, list)
                or not all(_valid_manifest_entry(entry) for entry in manifest)):
            return issues + ["invalid data.vocabularies in run start"], notices
        listed = {entry["sha256"]: entry for entry in manifest}
        supplied = {}
        for contract in unique:
            sha = contract.get("sha256")
            if sha is not None:
                supplied[sha] = contract
        for contract in unique:
            sha = contract.get("sha256")
            if sha is None or sha not in listed:
                issues.append(f"extra vocabulary file '{_display(contract)}' "
                              "not listed in run data.vocabularies")
        for entry in manifest:
            if entry["sha256"] not in supplied:
                issues.append(f"vocabulary '{entry['tool']}' in run "
                              "data.vocabularies not supplied "
                              f"(sha256 {entry['sha256']})")
        effective = []
        for sha, contract in supplied.items():
            if sha not in listed:
                continue  # Extra files never widen the allowed set.
            if contract.get("tool") != listed[sha].get("tool"):
                issues.append(
                    f"vocabulary file '{_display(contract)}' tool "
                    f"'{contract.get('tool')}' does not match run "
                    f"data.vocabularies tool '{listed[sha].get('tool')}'")
                continue
            effective.append(contract)
    # Cross-file consistency over the effective set, in first-seen order.
    for left_pos in range(len(effective)):
        for right_pos in range(left_pos + 1, len(effective)):
            left, right = effective[left_pos], effective[right_pos]
            if left.get("tool") == right.get("tool"):
                issues.append(
                    f"duplicate vocabulary tool '{left.get('tool')}' in "
                    f"'{_display(left)}' and '{_display(right)}'")
            for name in left.get("events", {}):
                if name in right.get("events", {}) and _canonical(
                        left["events"][name]) != _canonical(right["events"][name]):
                    issues.append(
                        f"conflicting event definition '{name}' in "
                        f"'{_display(left)}' and '{_display(right)}'")
            for command_id in (left.get("commands") or {}):
                if command_id in (right.get("commands") or {}):
                    issues.append(
                        f"duplicate command '{command_id}' in "
                        f"'{_display(left)}' and '{_display(right)}'")
    if single and rows[0]["tool"] != unique[0].get("tool"):
        issues.append("event list tool mismatch")
    command = rows[0]["data"].get("command") if command is None else command
    if single and manifest is None and unique[0].get("commands") is not None \
            and not isinstance(command, str):
        # Today's single-file check: a command contract needs a selection
        # before any per-row validation runs.
        return (issues + ["select a command with --command "
                           "(or run data.command) for this contract"], notices)
    declared = {}
    for contract in effective:
        for name, declaration in contract.get("events", {}).items():
            declared.setdefault(name, declaration)
    run_level = rows[0]["data"].get("level", 1)
    run_level = run_level if isinstance(run_level, int) and not isinstance(run_level, bool) else 1
    level_of = lambda name: _declaration_level(declared.get(name, {}))
    present, over = set(), set()
    for row in rows:
        name = row["event"]
        present.add(name)
        if name in declared and level_of(name) > run_level and name not in over:
            over.add(name)
            issues.append(f"level {level_of(name)} event {name} in level {run_level} run")
        declaration = declared.get(name)
        if declaration is None:
            issues.append(f"undeclared event {name}")
        elif declaration["kind"] != ("point" if row["state"] == "point" else "span"):
            issues.append(f"wrong event kind {name}")
    # Each vocabulary's required events and command rules apply only to the
    # vocabulary that declares them; one owner cannot fail another's runs.
    label = command if isinstance(command, str) else "run"
    declared_commands = set()
    for contract in effective:
        declared_commands.update((contract.get("commands") or {}).keys())
    if (isinstance(command, str) and declared_commands
            and command not in declared_commands):
        issues.append(f"undeclared command {command!r}")
    for contract in effective:
        required = [name for name in contract.get("required", [])
                    if _declaration_level(contract["events"][name]) <= run_level]
        commands = contract.get("commands")
        if commands is None:
            wanted = required
        elif not isinstance(command, str):
            continue  # Command-scoped rules need a command this file declares.
        elif command in commands:
            wanted = [*required, *[name for name in commands[command]
                                     if _declaration_level(contract["events"][name]) <= run_level]]
        else:
            continue  # Another owner's command; this file imposes nothing.
        for name in wanted:
            if name not in present:
                issues.append(f"missing required event {name} for {label}")
    return issues, notices


def read_events(path):
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"event list is not UTF-8: {exc}")
    contract = json.loads(text)
    if (not isinstance(contract, dict) or not isinstance(contract.get("tool"), str)
            or not isinstance(contract.get("events"), dict)
            or ("commands" in contract and not isinstance(contract["commands"], dict))):
        raise ValueError("event list requires tool and events, with optional commands")
    for name, event in contract["events"].items():
        if (not isinstance(event, dict) or event.get("kind") not in ("span", "point")
                or not isinstance(event.get("fields"), dict)
                or not all(isinstance(v, str) for v in event["fields"].values())):
            raise ValueError(f"invalid event declaration {name}")
        level = event.get("level", 1)
        if not isinstance(level, int) or isinstance(level, bool) or level < 1:
            raise ValueError(f"invalid level for event {name}")
    for command, names in [("required", contract.get("required", [])), *contract.get("commands", {}).items()]:
        if not isinstance(names, list) or not all(isinstance(n, str) and n in contract["events"] for n in names):
            raise ValueError(f"invalid required events for {command}")
    return {"tool": contract["tool"], "path": str(path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "events": contract["events"],
            "required": contract.get("required", []),
            "commands": contract.get("commands")}
