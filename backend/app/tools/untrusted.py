"""The ``<untrusted_content>`` wrapper (T1, SECURITY_MODEL §9; spec 3.1d2a Goal 2).

Repository content, file names, commit messages and search hits reach the model as data inside one wrapper the
gateway writes after redaction and cutting, so the closing tag is always there. Anything in the content that
looks like the wrapper's own tag is neutralised; the attributes are escaped. Code stays readable: only the tag
name is touched, not every ``<``.
"""

from __future__ import annotations

import re

TAG = "untrusted_content"
_FORGED = re.compile(r"<(?=\s*/?\s*untrusted_content)", re.IGNORECASE)
_ATTRIBUTE_MAX = 512
_ATTRIBUTE_ESCAPES = {"&": "&amp;", '"': "&quot;", "<": "&lt;", ">": "&gt;"}


def neutralise(text: str) -> str:
    """``<untrusted_content`` / ``</untrusted_content`` (any case, any spacing) lose their ``<``."""
    return _FORGED.sub("&lt;", text)


def attribute(value: str) -> str:
    """A value safe inside double quotes on one line: XML escapes, control characters dropped, bounded."""
    value = "".join(ch for ch in value[:_ATTRIBUTE_MAX] if ch >= " " and ch != "\x7f")
    return "".join(_ATTRIBUTE_ESCAPES.get(ch, ch) for ch in value)


def open_tag(source: str, path: str | None) -> str:
    where = f' path="{attribute(path)}"' if path is not None else ""
    return f'<{TAG} source="{attribute(source)}"{where}>\n'


CLOSE_TAG = f"\n</{TAG}>"
