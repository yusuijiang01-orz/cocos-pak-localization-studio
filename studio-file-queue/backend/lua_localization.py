from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class LuaString:
    ordinal: int
    start: int
    end: int
    quote: int
    content: bytes


@dataclass(frozen=True)
class LuaTextPart:
    ordinal: int
    start: int
    end: int
    quote: int
    content: bytes
    kind: str


LUA_TAG_RE = re.compile(br"<[^<>\r\n]{1,512}>")
def iter_lua_strings(line: bytes):
    """Yield quoted Lua strings outside comments, preserving byte offsets."""
    i = 0
    ordinal = 0
    size = len(line)
    while i < size:
        if line[i:i + 2] == b"--":
            return
        quote = line[i]
        if quote not in (34, 39):
            i += 1
            continue
        content_start = i + 1
        i = content_start
        escaped = False
        while i < size:
            byte = line[i]
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == quote:
                ordinal += 1
                yield LuaString(ordinal, content_start, i, quote, line[content_start:i])
                i += 1
                break
            i += 1
        else:
            return


def replace_lua_strings(line: bytes, replacements: dict[int, bytes]) -> tuple[bytes, set[int]]:
    literals = list(iter_lua_strings(line))
    applied = set()
    rebuilt = line
    for literal in reversed(literals):
        replacement = replacements.get(literal.ordinal)
        if replacement is None:
            continue
        quote = bytes((literal.quote,))
        # A raw matching quote would terminate the Lua literal. Existing escaped
        # quotes are left intact; newly introduced raw quotes are escaped.
        safe = bytearray()
        escaped = False
        for byte in replacement:
            if byte == literal.quote and not escaped:
                safe.extend(b"\\" + quote)
            else:
                safe.append(byte)
            if byte == 92 and not escaped:
                escaped = True
            else:
                escaped = False
        rebuilt = rebuilt[:literal.start] + bytes(safe) + rebuilt[literal.end:]
        applied.add(literal.ordinal)
    return rebuilt, applied


def iter_lua_text_parts(line: bytes):
    """Yield only player-visible spans from Lua literals.

    Markup is never exposed as translation text.  Position tags are special:
    their visible name is yielded, while the tag syntax and coordinates remain
    outside the editable span.
    """
    ordinal = 0
    for literal in iter_lua_strings(line):
        cursor = 0
        for tag in LUA_TAG_RE.finditer(literal.content):
            if tag.start() > cursor:
                content = literal.content[cursor:tag.start()]
                if content.strip():
                    ordinal += 1
                    yield LuaTextPart(ordinal, literal.start + cursor, literal.start + tag.start(), literal.quote, content, "text")
            # Everything inside angle brackets is runtime markup. Never expose
            # it as editable text, including pos/npcpos display-looking names.
            cursor = tag.end()
        if cursor < len(literal.content):
            content = literal.content[cursor:]
            if content.strip():
                ordinal += 1
                yield LuaTextPart(ordinal, literal.start + cursor, literal.end, literal.quote, content, "text")


def replace_lua_text_parts(line: bytes, replacements: dict[int, bytes]) -> tuple[bytes, set[int]]:
    parts = list(iter_lua_text_parts(line))
    applied = set()
    rebuilt = line
    for part in reversed(parts):
        replacement = replacements.get(part.ordinal)
        if replacement is None:
            continue
        quote = bytes((part.quote,))
        safe = replacement.replace(quote, b"\\" + quote)
        rebuilt = rebuilt[:part.start] + safe + rebuilt[part.end:]
        applied.add(part.ordinal)
    return rebuilt, applied


def lua_code_skeleton(line: bytes) -> bytes:
    """Return code with literal contents blanked, for structural validation."""
    rebuilt = line
    for literal in reversed(list(iter_lua_strings(line))):
        rebuilt = rebuilt[:literal.start] + rebuilt[literal.end:]
    return rebuilt


def lua_structure_skeleton(line: bytes) -> bytes:
    """Blank editable text while retaining Lua code, tags, and coordinates."""
    rebuilt = line
    for part in reversed(list(iter_lua_text_parts(line))):
        rebuilt = rebuilt[:part.start] + rebuilt[part.end:]
    return rebuilt
