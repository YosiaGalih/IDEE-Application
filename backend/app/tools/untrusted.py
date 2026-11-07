"""The ``<untrusted_content>`` wrapper (T1, SECURITY_MODEL §9; spec 3.1d2a Goal 2).

Repository content, file names, commit messages and search hits reach the model as data inside one wrapper the
gateway writes after redaction and cutting, so the closing tag is always there. Anything in the content that
looks like the wrapper's own tag is neutralised, also when invisible characters (zero-width, joiners, bidi
marks, U+FFFD from a NUL) or a fullwidth bracket are slipped in (AG-13 WP-3.1d2a LOW-2). Code stays readable:
only a bracket that opens such a tag is touched, not every ``<``. Attributes are cleaned of control characters
before redaction (LOW-4), then escaped and bounded.
"""

from __future__ import annotations

import re

TAG = "untrusted_content"
# Characters a renderer or a tokenizer may drop or ignore inside a tag (Cf format characters and friends).
_INVISIBLE = (
    "[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f\u202a-\u202e"
    "\u2060-\u206f\ufe00-\ufe0f\ufeff\ufffd]*"
)
_NAME = _INVISIBLE.join(re.escape(ch) for ch in TAG)
_SLASH = rf"{_INVISIBLE}\s*{_INVISIBLE}[/\uff0f]?{_INVISIBLE}\s*{_INVISIBLE}"
_FORGED = re.compile(rf"[<\uff1c\ufe64](?={_SLASH}{_NAME})", re.IGNORECASE)
ATTRIBUTE_MAX = 512
_ATTRIBUTE_ESCAPES = {"&": "&amp;", '"': "&quot;", "<": "&lt;", ">": "&gt;"}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def neutralise(text: str) -> str:
    """The bracket that opens an ``untrusted_content`` tag (opening or closing, any case, any invisible
    characters, fullwidth) becomes ``&lt;``."""
    return _FORGED.sub("&lt;", text)


def strip_controls(value: str) -> str:
    return _CONTROL.sub("", value)


def attribute(value: str) -> str:
    """A value safe inside double quotes on one line: control characters dropped (callers redact after
    that), XML escapes, at most ATTRIBUTE_MAX characters, never cut inside an entity."""
    escaped = "".join(_ATTRIBUTE_ESCAPES.get(ch, ch) for ch in strip_controls(value))
    if len(escaped) <= ATTRIBUTE_MAX:
        return escaped
    cut = escaped[:ATTRIBUTE_MAX]
    amp = cut.rfind("&")
    if amp != -1 and ";" not in cut[amp:]:
        cut = cut[:amp]
    return cut + "..."


def open_tag(source: str, path: str | None) -> str:
    where = f' path="{attribute(path)}"' if path is not None else ""
    return f'<{TAG} source="{attribute(source)}"{where}>\n'


CLOSE_TAG = f"\n</{TAG}>"
CUT_MARKER = "\n[cut]"
