"""Offset maps from a derived text (a *view*) back to the text it was derived from.

A view is built by walking the source text and either *keeping* stretches verbatim or
*replacing* a source range with new text. Only the segments are stored, so memory is
O(number of edits).

Projection rule (recall first): a view range maps to the smallest source range that covers
every source character of every view character it touches. Touching any part of a replaced
segment pulls in the whole source range of that segment.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Segment:
    view_start: int
    view_end: int
    src_start: int
    src_end: int
    identity: bool  # True: view text == source text over this segment (equal lengths)


class OffsetMap:
    """Maps view offsets to source offsets. Immutable once built."""

    __slots__ = ("_segments", "_starts", "src_len", "view_len")

    def __init__(self, segments: Sequence[Segment], view_len: int, src_len: int) -> None:
        self._segments = tuple(segments)
        # Only non-empty view segments can contain a view character.
        self._starts = [s.view_start for s in self._segments]
        self.view_len = view_len
        self.src_len = src_len

    @classmethod
    def identity(cls, n: int) -> OffsetMap:
        return cls([Segment(0, n, 0, n, True)] if n else [], n, n)

    @property
    def segments(self) -> tuple[Segment, ...]:
        return self._segments

    def _segment_at(self, pos: int) -> Segment:
        """The non-empty view segment containing view character ``pos``."""
        i = bisect.bisect_right(self._starts, pos) - 1
        # Skip back over zero-length (deletion) segments that share a start.
        while i >= 0:
            seg = self._segments[i]
            if seg.view_start <= pos < seg.view_end:
                return seg
            i -= 1
        raise IndexError(pos)

    def _char_src(self, pos: int) -> tuple[int, int]:
        seg = self._segment_at(pos)
        if seg.identity:
            o = seg.src_start + (pos - seg.view_start)
            return o, o + 1
        return seg.src_start, seg.src_end

    def project(self, start: int, end: int) -> tuple[int, int]:
        """Project the half-open view range ``[start, end)`` to a source range."""
        if not (0 <= start <= end <= self.view_len):
            raise ValueError("view range out of bounds")
        if start == end:
            return self.project_point(start), self.project_point(start)
        a, _ = self._char_src(start)
        _, b = self._char_src(end - 1)
        # Replaced segments can map to source ranges that are not monotone with view order
        # only if the builder were misused; it enforces monotonicity, so a <= b holds.
        return a, b

    def project_point(self, pos: int) -> int:
        """Project a view boundary position to a source boundary position."""
        if pos >= self.view_len:
            return self.src_len
        return self._char_src(pos)[0]


class ViewBuilder:
    """Builds a view text and its :class:`OffsetMap` by walking a source text left to right."""

    __slots__ = ("_parts", "_pos", "_segments", "_src", "_view_len")

    def __init__(self, src: str) -> None:
        self._src = src
        self._pos = 0
        self._parts: list[str] = []
        self._segments: list[Segment] = []
        self._view_len = 0

    def keep_to(self, src_end: int) -> None:
        """Copy source text verbatim up to ``src_end``."""
        if src_end < self._pos or src_end > len(self._src):
            raise ValueError("keep_to out of order")
        if src_end == self._pos:
            return
        n = src_end - self._pos
        last = self._segments[-1] if self._segments else None
        if last is not None and last.identity and last.src_end == self._pos:
            self._segments[-1] = Segment(
                last.view_start, last.view_end + n, last.src_start, src_end, True
            )
        else:
            self._segments.append(
                Segment(self._view_len, self._view_len + n, self._pos, src_end, True)
            )
        self._parts.append(self._src[self._pos : src_end])
        self._view_len += n
        self._pos = src_end

    def replace(self, src_start: int, src_end: int, new: str) -> None:
        """Replace source ``[src_start, src_end)`` with ``new`` (which may be empty)."""
        if src_start < self._pos or src_end < src_start or src_end > len(self._src):
            raise ValueError("replace out of order")
        if src_start == src_end:
            raise ValueError("insertion without a source range is not allowed")
        self.keep_to(src_start)
        if new == self._src[src_start:src_end]:
            self.keep_to(src_end)
            return
        self._segments.append(
            Segment(self._view_len, self._view_len + len(new), src_start, src_end, False)
        )
        self._parts.append(new)
        self._view_len += len(new)
        self._pos = src_end

    def build(self) -> tuple[str, OffsetMap]:
        self.keep_to(len(self._src))
        return "".join(self._parts), OffsetMap(self._segments, self._view_len, len(self._src))


def apply_edits(src: str, edits: Iterable[tuple[int, int, str]]) -> tuple[str, OffsetMap]:
    """Apply sorted, non-overlapping ``(start, end, replacement)`` edits to ``src``."""
    b = ViewBuilder(src)
    for start, end, new in sorted(edits, key=lambda e: (e[0], e[1])):
        b.replace(start, end, new)
    return b.build()


class OffsetChain:
    """A sequence of maps applied innermost-last: view_n -> ... -> view_1 -> source."""

    __slots__ = ("maps",)

    def __init__(self, maps: Sequence[OffsetMap]) -> None:
        # maps[0] maps view_1 -> source, maps[-1] maps the final view -> previous view.
        self.maps = tuple(maps)

    def project(self, start: int, end: int) -> tuple[int, int]:
        for m in reversed(self.maps):
            start, end = m.project(start, end)
        return start, end

    def project_point(self, pos: int) -> int:
        for m in reversed(self.maps):
            pos = m.project_point(pos)
        return pos
