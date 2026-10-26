# SPDX-License-Identifier: MIT
"""Synchronous, single-process JSONL writer."""
import json
import math
import os
import re
import secrets
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .sanitize import sanitize_argv

CONTEXT_ENV = "FLATTRACE_TRACEPARENT"
LEVEL_ENV = "FLATTRACE_LEVEL"
_warned = False


def warn(exc, *, disabled=True):
    global _warned
    if _warned != os.getpid():
        _warned = os.getpid()
        try:
            prefix = "tracing disabled: " if disabled else ""
            print(f"flattrace: {prefix}{exc}", file=sys.stderr)
        except (OSError, ValueError, UnicodeError):
            pass  # A closed stderr must not stop product work either.


def filename(tool, run):
    if not re.fullmatch(r"[a-z0-9_-]+", tool):
        raise ValueError("environment writer tool must be a lowercase slug")
    now = datetime.now(timezone.utc)
    # Shared filename precision is milliseconds, the Node Date resolution.
    stamp = now.strftime("%Y%m%dT%H%M%S.") + f"{now.microsecond // 1000:03}Z"
    return f"{stamp}-{tool}-{run}.jsonl"


def add_note(data, note):
    existing = data.get("notes", [])
    existing = existing if isinstance(existing, list) else [existing]
    return {**data, "notes": [*existing, note]}


def representation(value):
    try:
        return repr(value)
    except Exception:
        return "<unserializable value>"


def normalize(value, notes, active=None):
    """Copy JSON values once; replace only the parts that cannot be recorded."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        notes.add("non-finite number replaced with null")
        return None
    if not isinstance(value, (dict, list, tuple)):
        notes.add("unserializable value replaced with text")
        return representation(value)
    if active is None:
        active = set()
    identity = id(value)
    if identity in active:
        notes.add("circular reference replaced with text")
        return "<circular reference>"
    active.add(identity)
    try:
        if isinstance(value, dict):
            if all(isinstance(key, str) for key in value):
                return {key: normalize(item, notes, active) for key, item in value.items()}
            # Pairs preserve keys without stringification collisions (1 versus "1").
            notes.add("non-string object keys represented as pairs")
            return [[normalize(key, notes, active), normalize(item, notes, active)]
                    for key, item in value.items()]
        return [normalize(item, notes, active) for item in value]
    except Exception:
        notes.add("unserializable value replaced with text")
        return representation(value)
    finally:
        active.remove(identity)


def payload(value, notes):
    value = normalize(value, notes)
    if not isinstance(value, dict):
        notes.add("non-object data wrapped in value")
        value = {"value": value}
    return value


def context(value):
    # Widths and nonzero IDs are from W3C traceparent, not workload limits.
    match = re.fullmatch(r"00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})", value)
    if not match or int(match[1], 16) == 0 or int(match[2], 16) == 0:
        raise ValueError("invalid FLATTRACE_TRACEPARENT")
    return match.groups()


def level_from_env():
    """FLATTRACE_LEVEL: 0 off, 1 default (rich, no noise), 2+ debug firehose."""
    raw = os.environ.get(LEVEL_ENV, "").strip()
    if not raw:
        return 1, None
    if raw.isascii() and raw.isdigit():
        try:
            return int(raw), None
        except ValueError:
            pass  # Python's configured integer-string conversion limit.
    return 1, f"invalid {LEVEL_ENV} {raw!r} treated as 1"


def new_id(size):
    value = secrets.token_hex(size)
    return value if int(value, 16) else new_id(size)


class Writer:
    @classmethod
    def from_env(cls, tool, tool_version, *, argv=None, data=None):
        directory = os.environ.get("FLATTRACE_DIR")
        level, note = level_from_env()
        if not directory or level == 0:
            return None
        notes = set()
        data = payload({} if data is None else data, notes)
        for message in sorted(notes):
            data = add_note(data, message)
        data = {**data, "level": level}
        if note is not None:
            data = add_note(data, note)
        try:
            Path(directory).mkdir(parents=True, exist_ok=True)
            writer = cls(None, tool, tool_version, argv=argv, data=data, _directory=directory)
            writer.level = level
            return writer
        except OSError as exc:
            warn(exc)
            return None

    def __init__(self, path, tool, tool_version, *, argv=None, data=None, _directory=None):
        if not isinstance(tool, str) or not isinstance(tool_version, str):
            raise TypeError("tool and tool_version must be strings")
        inherited = os.environ.get(CONTEXT_ENV)
        self.trace, self.parent, self.flags = (
            context(inherited) if inherited is not None else (new_id(16), None, "00"))
        self.tool, self.tool_version = tool, tool_version
        self.level = 1  # from_env sets the FLATTRACE_LEVEL value.
        self.run, self.root = new_id(16), new_id(8)
        self.active = {}
        self.seq = 0
        self.pid = os.getpid()
        self.best_effort = _directory is not None
        while True:
            self.path = Path(_directory) / filename(tool, self.run) if _directory is not None else Path(path)
            try:
                self.file = open(self.path, "x", encoding="utf-8")
                break
            except FileExistsError:
                if _directory is None:
                    raise
                self.run = new_id(16)
        notes = set()
        data = payload({} if data is None else data, notes)
        for note in sorted(notes):
            data = add_note(data, note)
        self._start("run", {
            **data,
            "argv": sanitize_argv([] if argv is None else argv),
            "host": socket.gethostname(), "version": tool_version, "pid": self.pid,
        }, self.parent, self.root)

    def _write(self, event, state, span, parent, data, **extra):
        if not isinstance(event, str):
            raise TypeError("event must be a string")
        if os.getpid() != self.pid:
            warn("writer inherited across fork; create a new writer")
            if self.file is not None:
                self.file.close()
                self.file = None
            return
        error = extra.get("error")
        if error is not None and (not isinstance(error, dict)
                or not all(isinstance(error.get(key), str) for key in ("kind", "message"))):
            raise TypeError("error requires string kind and message")
        notes = set()
        data = payload({} if data is None else data, notes)
        extra = normalize(extra, notes)
        if error is not None and not isinstance(extra["error"], dict):
            extra["error"] = {"kind": error["kind"], "message": error["message"],
                              "context": extra["error"]}
        for note in sorted(notes):
            data = add_note(data, note)
        row = dict(v=1, seq=self.seq, tool=self.tool, tool_version=self.tool_version,
                   run=self.run, trace=self.trace, span=span, parent=parent,
                   event=event, state=state,
                   t=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                   data=data, **extra)
        # Full envelope validation belongs to the offline checker, not each write.
        try:
            line = json.dumps(row, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError, OverflowError, RecursionError):
            # Includes Python's configured integer-string limit; keep the event.
            row["data"] = {"value": representation(data),
                           "notes": ["unserializable value replaced with text"]}
            if "error" in row:
                row["error"] = {"kind": "serialization", "message": representation(row["error"])}
            line = json.dumps(row, ensure_ascii=False, allow_nan=False)
        self.seq += 1
        # All UTF-16 surrogates, not just surrogateescape's byte subset, are safe.
        if not line.isascii():
            line = re.sub(r"[\ud800-\udfff]", "\ufffd", line)
        if self.file is not None:
            try:
                self.file.write(line + "\n")
                self.file.flush()
            except OSError as exc:
                if not self.best_effort:
                    raise
                warn(exc)
                try:
                    self.file.close()
                except OSError:
                    pass
                self.file = None

    def _start(self, event, data, parent, span):
        self._write(event, "start", span, parent, data)
        self.active[span] = (event, parent, time.perf_counter())
        return span

    def start(self, event, data=None, *, parent=None):
        if event == "run":
            raise ValueError("run is reserved")
        parent = self.root if parent is None else parent
        if parent not in self.active:
            raise ValueError("inactive parent")
        return self._start(event, data, parent, new_id(8))

    def point(self, event, data=None, *, span=None, error=None):
        if event == "run":
            raise ValueError("run is reserved")
        span = self.root if span is None else span
        if span not in self.active:
            if self.root not in self.active:
                return  # Closed run: never reopen it for a late point.
            notes = set()
            data = payload({} if data is None else data, notes)
            for note in sorted(notes):
                data = add_note(data, note)
            data = add_note({**data, "late_span": span}, "point after span end")
            span = self.root
        extra = {} if error is None else {"error": error}
        self._write(event, "point", span, self.active[span][1], data, **extra)

    def end(self, span, outcome, data=None, *, error=None):
        if span == self.root:
            message = "run root can only end through close()"
            if not self.best_effort:
                raise ValueError(message)
            warn(message, disabled=False)
            return
        self._end(span, outcome, data, error=error)

    def _end(self, span, outcome, data=None, *, error=None):
        if not isinstance(outcome, str):
            raise TypeError("outcome must be a string")
        if span not in self.active:
            raise ValueError("inactive span")
        event, parent, begun = self.active[span]
        extra = {} if error is None else {"error": error}
        self._write(event, "end", span, parent, data,
                    took_ms=(time.perf_counter() - begun) * 1000,
                    outcome=outcome, **extra)
        del self.active[span]

    def traceparent(self, span=None):
        span = self.root if span is None else span
        if span not in self.active:
            raise ValueError("inactive span")
        return f"00-{self.trace}-{span}-{self.flags}"

    def child_env(self, span=None):
        return {**os.environ, CONTEXT_ENV: self.traceparent(span)}

    def close(self, outcome="ok", data=None):
        """End the run. Optional ``data`` is the run-end payload (e.g. totals)."""
        if any(span != self.root for span in self.active):
            raise ValueError("local spans still active")
        self._end(self.root, outcome, data)
        if self.file is not None:
            try:
                self.file.close()
            except OSError as exc:
                if not self.best_effort:
                    raise
                warn(exc)
