# SPDX-License-Identifier: MIT
"""Copy JSONL, substituting only paths, hosts and user names."""
import getpass
import json
import re
from pathlib import Path

from .check import read_trace
from .sanitize import LONE_SURROGATE, credential_text, sanitize_argv, SECRET_KEY

# Absolute Unix/Windows paths and explicit ./, ../, ~/ paths in text.
# For ambiguous relative paths or paths containing spaces, supply --path.
PATH = re.compile(r"""(?<![\w:/\\])(?:[A-Za-z]:[\\/]|/|\.\.?/|~/)[^\s"'<>|,;)\]}]+""")


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings(item)


def safe_text(value):
    """Replace lone surrogates with U+FFFD so export serializes safely.

    Writers since 0.3.0 emit only valid Unicode scalar strings; legacy lines
    may still carry lone-surrogate escapes, which the checker reports as a
    warning. Export records the same replacement the writer would have made.
    """
    if isinstance(value, str):
        return LONE_SURROGATE.sub("\ufffd", value)
    if isinstance(value, list):
        return [safe_text(item) for item in value]
    if isinstance(value, dict):
        return {safe_text(key): safe_text(item) for key, item in value.items()}
    return value


def export(files, out, *, paths=(), hosts=(), users=()):
    def credentials(value):
        if isinstance(value, str):
            return credential_text(value)
        if isinstance(value, list):
            return [credentials(item) for item in value]
        if isinstance(value, dict):
            result = {credential_text(key): "<redacted>" if SECRET_KEY.fullmatch(key) else credentials(
                      sanitize_argv(item) if key == "argv" and isinstance(item, list)
                      and all(isinstance(arg, str) for arg in item) else item) for key, item in value.items()}
            if len(result) != len(value):
                raise ValueError("credential-masked object keys collide")
            return result
        return value

    inputs = [(path, *read_trace(path)) for path in files]
    for path, _, issues, _ in inputs:
        invalid = [issue for issue in issues if not issue.startswith(("unfinished run ", "unfinished span ", "incomplete tail "))]
        if invalid:
            raise ValueError(f"{path}: {'; '.join(invalid)}; export refused")
    inputs = [(path, [credentials(row) for row in rows], issues, notices) for path, rows, issues, notices in inputs]
    names = {"path": dict.fromkeys(paths), "host": dict.fromkeys(hosts),
             "user": dict.fromkeys([getpass.getuser(), *users])}
    for _, rows, _, _ in inputs:
        for row in rows:
            if row["event"] == "run" and row["state"] == "start":
                names["host"][row["data"]["host"]] = None
                env = row["data"].get("env")
                if isinstance(env, dict) and isinstance(env.get("host.name"), str):
                    names["host"][env["host.name"]] = None
            for text in strings(row):
                for match in PATH.finditer(text):
                    value = match[0]
                    if value not in names["path"]:
                        value = value.rstrip(".:!?")
                    names["path"][value] = None
    replacements = {}
    for kind, values in names.items():
        for number, value in enumerate(filter(None, values), 1):
            replacements[value] = f"<{kind}-{number}>"
    alternatives = []
    for value in sorted(replacements, key=len, reverse=True):
        literal = re.escape(value)
        alternatives.append(literal if value in names["path"] else rf"(?<![\w.-]){literal}(?![\w.-])")
    pattern = re.compile("|".join(alternatives)) if alternatives else None

    def replace(value, *, keys=()):
        if isinstance(value, str):
            return pattern.sub(lambda m: replacements[m[0]], value) if pattern else value
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            result = {(key if key in keys else replace(key)): replace(item) for key, item in value.items()}
            if len(result) != len(value):
                raise ValueError("redacted object keys collide")
            return result
        return value

    out = Path(out)
    out.mkdir()  # Separate fresh output, never modify the input files.
    issues = []
    staged = []
    try:
        for number, (path, rows, problems, _) in enumerate(inputs, 1):
            tmp = out / f"{number}.jsonl.tmp"
            with tmp.open("x", encoding="utf-8") as stream:
                for row in rows:
                    # Envelope names/IDs/categories are structural, not private text.
                    shared = dict(row)
                    header = row["event"] == "run" and row["state"] == "start"
                    public_keys = ("argv", "host", "version", "pid", "command") if header else ()
                    if row["event"] == "verdict":
                        public_keys = ("trace", "run", "span", "name", "correct", "score", "reward", "reason", "grader")
                    shared["data"] = replace(row["data"], keys=(*public_keys, "retry_of", "links", "env", "notes", "late_span"))
                    for identity in ("retry_of", "late_span"):
                        if identity in row["data"]:
                            shared["data"][identity] = row["data"][identity]
                    links = row["data"].get("links")
                    if isinstance(links, list):
                        shared["data"]["links"] = []
                        for link in links:
                            item = replace(link, keys=("relation", "trace", "span"))
                            if isinstance(link, dict):
                                item.update({key: link[key] for key in ("relation", "trace", "span") if key in link})
                            shared["data"]["links"].append(item)
                    env = row["data"].get("env")
                    if isinstance(env, dict):
                        shared["data"]["env"] = replace(env, keys=tuple(env))
                    if row["event"] == "verdict":
                        for key in ("trace", "run", "span"):
                            if key in row["data"]:
                                shared["data"][key] = row["data"][key]
                        grader = row["data"].get("grader")
                        if isinstance(grader, dict):
                            shared["data"]["grader"] = replace(grader, keys=("name", "version", "kind"))
                    if header:
                        shared["data"]["version"] = row["tool_version"]
                        if "command" in row["data"]:
                            shared["data"]["command"] = row["data"]["command"]
                    if "error" in row:
                        shared["error"] = replace(row["error"], keys=("kind", "message"))
                        shared["error"]["kind"] = row["error"]["kind"]
                    for key in row.keys() - {"v", "seq", "tool", "tool_version", "run", "trace", "span", "parent",
                                             "event", "state", "t", "took_ms", "outcome", "data", "error"}:
                        shared[key] = replace(row[key])
                    stream.write(json.dumps(safe_text(shared), ensure_ascii=False, allow_nan=False) + "\n")
            staged.append((tmp, out / f"{number}.jsonl"))
            if any(issue.startswith("incomplete tail ") for issue in problems):
                note_tmp = out / f"{number}.tail.txt.tmp"
                note_tmp.write_text("Incomplete final line omitted from this export; original retained locally.\n",
                                    encoding="utf-8")
                staged.append((note_tmp, out / f"{number}.tail.txt"))
            issues.extend(f"{path}: {issue}" for issue in problems)
    except Exception:
        # A failed export leaves no plausible partial output behind.
        for leftover in out.glob("*.tmp"):
            leftover.unlink(missing_ok=True)
        raise
    for tmp, final in staged:
        tmp.rename(final)
    return issues
