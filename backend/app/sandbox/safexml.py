"""XML from a sandbox run is untrusted: parsed with expat, no DTD, no entities, bounded size."""

from __future__ import annotations

import xml.etree.ElementTree as ET  # only TreeBuilder: parsing goes through the guarded expat below
from typing import NoReturn
from xml.parsers import expat

MAX_XML_BYTES = 1024 * 1024


class UnsafeXML(ValueError):
    """The document is too large, malformed, or declares a DTD or an entity."""


def parse(data: bytes) -> ET.Element:
    if len(data) > MAX_XML_BYTES:
        raise UnsafeXML("document too large")
    builder = ET.TreeBuilder()
    parser = expat.ParserCreate()

    def refuse(*_: object) -> NoReturn:
        raise UnsafeXML("DTD and entity declarations are not allowed")

    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.ExternalEntityRefHandler = refuse
    parser.StartElementHandler = builder.start
    parser.EndElementHandler = builder.end
    parser.CharacterDataHandler = builder.data
    try:
        parser.Parse(data, True)
        return builder.close()
    except UnsafeXML:
        raise
    except expat.ExpatError as exc:
        raise UnsafeXML("malformed XML") from exc
    except (ValueError, LookupError) as exc:  # e.g. encoding="shift_jis" (multi-byte) or an unknown encoding
        raise UnsafeXML("unsupported XML encoding") from exc
    except AssertionError as exc:  # TreeBuilder.close() on an empty or unbalanced document
        raise UnsafeXML("malformed XML") from exc
