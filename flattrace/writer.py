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

from .check import validate_event
from .sanitize import sanitize_argv

CONTEXT_ENV = "FLATTRACE_TRACEPARENT"
LEVEL_ENV = "FLATTRACE_LEVEL"
_warned = False


def warn(exc):
    global _warned
    if _warned != os.getpid():
        _warned = os.getpid()
        try:
            print(f"flattrace: tracing disabled: {exc}", file=sys.stderr)
        except (OSError, ValueError):
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


def numbers(value):
    """Return JSON-safe numbers and whether a non-finite value was replaced."""
    if isinstance(value, float) and not math.isfinite(value):
        return None, True
    if isinstance(value, (dict, list, tuple)):
        items = value.items() if isinstance(value, dict) else enumerate(value)
        result, changed = {}, False
        for key, item in items:
            result[key], replaced = numbers(item)
            changed |= replaced
        return (result if isinstance(value, dict) else list(result.values())), changed
    return value, False


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
    if raw.isdigit():
        return int(raw), None
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
        data = {**({} if data is None else data), "level": level}
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
        self._start("run", {
            **({} if data is None else data),
            "argv": sanitize_argv([] if argv is None else argv),
            "host": socket.gethostname(), "version": tool_version, "pid": self.pid,
        }, self.parent, self.root)

    def _write(self, event, state, span, parent, data, **extra):
        if os.getpid() != self.pid:
            warn("writer inherited across fork; create a new writer")
            if self.file is not None:
                self.file.close()
                self.file = None
            return
        data, changed = numbers({} if data is None else data)
        extra, extra_changed = numbers(extra)
        if changed or extra_changed:
            data = add_note(data, "non-finite number replaced with null")
        row = dict(v=1, seq=self.seq, tool=self.tool, tool_version=self.tool_version,
                   run=self.run, trace=self.trace, span=span, parent=parent,
                   event=event, state=state,
                   t=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                   data=data, **extra)
        validate_event(row)
        self.seq += 1
        # Unix surrogate-escaped filenames become ordinary lossy UTF-8 text.
        line = json.dumps(row, ensure_ascii=False, allow_nan=False)
        line = line.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
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
            data = add_note({**({} if data is None else data), "late_span": span}, "point after span end")
            span = self.root
        extra = {} if error is None else {"error": error}
        self._write(event, "point", span, self.active[span][1], data, **extra)

    def end(self, span, outcome, data=None, *, error=None):
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

    def close(self, outcome="ok"):
        if any(span != self.root for span in self.active):
            raise ValueError("local spans still active")
        self.end(self.root, outcome)
        if self.file is not None:
            try:
                self.file.close()
            except OSError as exc:
                if not self.best_effort:
                    raise
                warn(exc)
