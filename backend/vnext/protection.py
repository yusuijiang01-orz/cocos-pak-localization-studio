from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from protected_segments import PATTERN as LEGACY_PROTECTED_PATTERN


@dataclass(frozen=True)
class SkeletonPiece:
    kind: str  # "text" or "protected"
    value: str

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "value": self.value}


def split_runtime_text(value: str) -> list[SkeletonPiece]:
    text = str(value or "")
    pieces: list[SkeletonPiece] = []
    cursor = 0
    for match in LEGACY_PROTECTED_PATTERN.finditer(text):
        if match.start() > cursor:
            pieces.append(SkeletonPiece("text", text[cursor:match.start()]))
        pieces.append(SkeletonPiece("protected", match.group(0)))
        cursor = match.end()
    if cursor < len(text):
        pieces.append(SkeletonPiece("text", text[cursor:]))
    if not pieces:
        pieces.append(SkeletonPiece("text", text))
    return pieces


def protected_signature(pieces: Iterable[SkeletonPiece]) -> tuple[str, ...]:
    return tuple(piece.value for piece in pieces if piece.kind == "protected")


def reconstruct(pieces: Iterable[SkeletonPiece], translated_texts: Iterable[str]) -> str:
    translated = iter(translated_texts)
    output: list[str] = []
    consumed = 0
    for piece in pieces:
        if piece.kind == "protected":
            output.append(piece.value)
        elif piece.kind == "text":
            try:
                output.append(next(translated))
            except StopIteration as exc:
                raise ValueError("not enough translated spans for skeleton") from exc
            consumed += 1
        else:
            raise ValueError(f"unknown skeleton kind: {piece.kind}")
    try:
        next(translated)
    except StopIteration:
        return "".join(output)
    raise ValueError(f"too many translated spans for skeleton after {consumed} text pieces")
