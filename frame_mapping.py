"""Exact temporal sampling without copying an IMAGE batch."""

from collections.abc import Iterator, Sequence
from fractions import Fraction
from typing import Any
import re

UNSPECIFIED = {"", "0", "default", "auto", "none"}
MAX_DIMENSION = 8192


def parse_frames(value: str | None, source_frames: int) -> int:
    """Parse an optional positive frame count."""
    text = "" if value is None else str(value).strip().lower()
    if text in UNSPECIFIED:
        return source_frames
    if not re.fullmatch(r"[0-9]+", text) or int(text) <= 0:
        raise ValueError("target_frames must be a positive integer or empty/default/auto/none/0")
    count = int(text)
    if count == 1 and source_frames > 1:
        raise ValueError("target_frames=1 cannot preserve both source endpoints")
    return count


def parse_fps(value: str) -> Fraction:
    """Parse an exact positive rational or decimal frame rate."""
    try:
        fps = Fraction(str(value).strip())
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"Invalid FPS: {value!r}") from exc
    if not 0 < fps <= 1000:
        raise ValueError("FPS must be greater than zero and at most 1000")
    return fps


def dimensions(source: tuple[int, int], width: str, height: str) -> tuple[int, int]:
    """Resolve even target dimensions; round positive ties upward."""
    requested = []
    for name, value in (("width", width), ("height", height)):
        text = str(value).strip()
        if not text:
            requested.append(None)
        elif not re.fullmatch(r"[0-9]+", text) or not 2 <= int(text) <= MAX_DIMENSION:
            raise ValueError(f"{name} must be empty or an even integer in [2, {MAX_DIMENSION}]")
        elif int(text) % 2:
            raise ValueError(f"Explicit {name} must be even")
        else:
            requested.append(int(text))
    sw, sh = source
    w, h = requested
    if w is None and h is None:
        w, h = sw + sw % 2, sh + sh % 2
    elif w is None:
        w = max(2, 2 * ((h * sw + sh) // (2 * sh)))
    elif h is None:
        h = max(2, 2 * ((w * sh + sw) // (2 * sw)))
    if not 2 <= w <= MAX_DIMENSION or not 2 <= h <= MAX_DIMENSION:
        raise ValueError(f"Resolved dimensions must be in [2, {MAX_DIMENSION}]")
    return w, h


class FrameMapping(Sequence):
    """Expose deterministic nearest frames with half ties rounded upward."""

    def __init__(self, source: Any, count: int) -> None:
        """Keep a reference to the original batch and validate endpoints."""
        if len(source) < 1 or count < 1 or (count == 1 and len(source) > 1):
            raise ValueError("Cannot construct an endpoint-preserving frame mapping")
        self.source = source
        self.count = count

    def __len__(self) -> int:
        """Return the exact output frame count."""
        return self.count

    def source_index(self, index: int) -> int:
        """Return a source index using integer arithmetic only."""
        if index < 0:
            index += self.count
        if not 0 <= index < self.count:
            raise IndexError(index)
        if self.count == 1:
            return 0
        denominator = self.count - 1
        return (2 * index * (len(self.source) - 1) + denominator) // (2 * denominator)

    def __getitem__(self, index: int | slice) -> Any:
        """Fetch one frame or a requested slice without duplicating the batch."""
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(self.count))]
        return self.source[self.source_index(index)]

    def __iter__(self) -> Iterator[Any]:
        """Yield source frame references in output order."""
        for index in range(self.count):
            yield self[index]
