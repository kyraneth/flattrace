# SPDX-License-Identifier: MIT
"""Small known-credential masking pass; deliberately not a secret detector."""
import re

# Names are patterns for common credential arguments/text, not a list of all secrets.
SECRET_NAME = r"(?:token|password|passwd|secret|api[-_]?key|access[-_]?key)"
NAME = rf"(?:{SECRET_NAME}|key)"
FLAG = re.compile(rf"^-*{NAME}$", re.I)
SECRET_KEY = re.compile(rf"(?:{SECRET_NAME}|authorization)", re.I)
ASSIGNMENT = re.compile(rf"(\b{NAME}\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;&]+)", re.I)
AUTH = re.compile(r"(\bAuthorization\s*:\s*)(?:Bearer|Basic)\s+[^\s,;]+", re.I)
URL_AUTH = re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@", re.I)
FLAG_TEXT = re.compile(rf"(-+{NAME}\s+)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)", re.I)


def credential_text(value):
    value = AUTH.sub(r"\1<redacted>", value)
    value = URL_AUTH.sub(r"\1<redacted>@", value)
    value = ASSIGNMENT.sub(r"\1<redacted>", value)
    return FLAG_TEXT.sub(r"\1<redacted>", value)


def sanitize_argv(argv):
    """Tools must additionally sanitize their own custom secret options."""
    result, hide_next = [], False
    for value in argv:
        if hide_next:
            result.append("<redacted>")
            hide_next = False
        elif "=" in value and FLAG.fullmatch(value.partition("=")[0]):
            result.append(value.partition("=")[0] + "=<redacted>")
        else:
            result.append(credential_text(value))
            hide_next = bool(FLAG.fullmatch(value))
    return result
