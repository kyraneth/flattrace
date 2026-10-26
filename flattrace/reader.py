# SPDX-License-Identifier: MIT
"""Optional compiled reader engine for flattrace v1.

Importing this module does not require the engine, but calling any function
does. Without the compiled ``_flattrace_read`` extension (abi3 wheels carry
it; the pure ``py3-none-any`` fallback does not), every function raises
:exc:`EngineUnavailable`. ``from flattrace import Writer`` and the pure
check/export/stats entry points never touch this module.
"""
import importlib
import json
import os
import platform
import sys

__all__ = ["EngineUnavailable", "check", "stats", "spans", "digest",
           "group", "tree", "compare", "overlap"]


class EngineUnavailable(ImportError):
    """The compiled reader engine is not installed for this interpreter."""


def _engine():
    try:
        return importlib.import_module("flattrace._flattrace_read")
    except ImportError as exc:
        system, machine = platform.system(), platform.machine()
        raise EngineUnavailable(
            "flattrace reader engine unavailable: no compiled "
            f"_flattrace_read for {system}-{machine} on Python "
            f"{sys.version_info.major}.{sys.version_info.minor} "
            f"(loader failed with: {exc}); install a platform abi3 wheel "
            "instead of the pure py3-none-any fallback"
        ) from exc


def _paths(paths):
    return [os.fspath(path) for path in paths]


def _dump(value):
    return None if value is None else json.dumps(value, allow_nan=False)


def check(paths, events=None, command=None):
    """Per-file validation; mirrors ``flattrace check`` (see docs/reader.md)."""
    events = None if events is None else os.fspath(events)
    return json.loads(_engine().check(_paths(paths), events, command))


def stats(paths):
    """Corpus statistics; mirrors ``flattrace stats`` (see docs/reader.md)."""
    return json.loads(_engine().stats(_paths(paths)))


def spans(paths, filter=None):
    """Paired span rows with an optional filter (see docs/reader.md)."""
    return json.loads(_engine().spans(_paths(paths), _dump(filter)))


def digest(paths, scope=None):
    """Compact per-event table, error census and coverage (see docs/reader.md)."""
    return json.loads(_engine().digest(_paths(paths), _dump(scope)))


def group(paths, by, metrics=None, filter=None):
    """Group paired rows by key specs with metrics (see docs/reader.md)."""
    return json.loads(_engine().group(_paths(paths), list(by), _dump(metrics), _dump(filter)))


def tree(paths, trace):
    """Process tree for one trace ID across the corpus (see docs/reader.md)."""
    return json.loads(_engine().tree(_paths(paths), trace))


def compare(a_paths, b_paths, filter=None):
    """Two-corpus comparison with deltas (see docs/reader.md)."""
    return json.loads(_engine().compare(_paths(a_paths), _paths(b_paths), _dump(filter)))


def overlap(paths, window, filter=None):
    """Wall-timestamp overlap of spans with window spans (see docs/reader.md)."""
    return json.loads(_engine().overlap(_paths(paths), window, _dump(filter)))
