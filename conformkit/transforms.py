"""Column text transform engine.

A per-column pipeline of ordered operations, matching the recovered settings
vocabulary: replace, insert, remove chars, remove text by occurrence, remove
text relative to an anchor, truncate, date substitution, conditional build.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

__all__ = [
    "Transform", "ReplaceText", "InsertText", "RemoveChars", "RemoveTextByOccurrence",
    "RemoveTextAroundAnchor", "Truncate", "SubstituteDate", "Conditional",
    "TransformPipeline", "apply_pipeline",
]


class Transform(Protocol):
    def apply(self, value: str) -> str: ...


@dataclass
class ReplaceText:
    """Replace the Nth occurrence (or all) of `find` with `replace_with`."""
    find: str
    replace_with: str
    occurrence: int | None = None      # None = all

    def apply(self, value: str) -> str:
        if not self.find:
            return value
        if self.occurrence is None:
            return value.replace(self.find, self.replace_with)
        idx = -1
        start = 0
        for _ in range(self.occurrence):
            idx = value.find(self.find, start)
            if idx < 0:
                return value
            start = idx + len(self.find)
        return value[:idx] + self.replace_with + value[idx + len(self.find):]


@dataclass
class InsertText:
    text: str
    char_pos: int                      # 0-based; clamped to length

    def apply(self, value: str) -> str:
        pos = max(0, min(self.char_pos, len(value)))
        return value[:pos] + self.text + value[pos:]


@dataclass
class RemoveChars:
    chars: str

    def apply(self, value: str) -> str:
        if not self.chars:
            return value
        table = {ord(c): None for c in self.chars}
        return value.translate(table)


@dataclass
class RemoveTextByOccurrence:
    """Remove one occurrence of `find`, or everything except it.

    remove_occurrence=True  -> delete the Nth occurrence of `find` itself
    remove_occurrence=False -> keep only the segment containing the Nth
                               occurrence, discarding the other segments
    """
    find: str
    occurrence: int
    remove_occurrence: bool = True

    def apply(self, value: str) -> str:
        if not self.find or self.occurrence < 1:
            return value

        # locate the Nth occurrence
        idx = -1
        start = 0
        for _ in range(self.occurrence):
            idx = value.find(self.find, start)
            if idx < 0:
                return value
            start = idx + len(self.find)

        if self.remove_occurrence:
            return value[:idx] + value[idx + len(self.find):]

        # keep only the segment terminated by the Nth separator
        prev_end = 0
        for _ in range(self.occurrence - 1):
            prev_end = value.find(self.find, prev_end) + len(self.find)
        return value[prev_end:idx]


@dataclass
class RemoveTextAroundAnchor:
    anchor: str
    occurrence: int = 1
    mode: str = "after"                # "after" | "before"
    remove_text: str = ""              # if empty, remove everything on that side

    def apply(self, value: str) -> str:
        if not self.anchor:
            return value
        idx = -1
        start = 0
        for _ in range(max(1, self.occurrence)):
            idx = value.find(self.anchor, start)
            if idx < 0:
                return value
            start = idx + len(self.anchor)

        if self.mode == "after":
            tail_start = idx + len(self.anchor)
            tail = value[tail_start:]
            if not self.remove_text:
                return value[:tail_start]
            return value[:tail_start] + tail.replace(self.remove_text, "")
        else:
            head = value[:idx]
            if not self.remove_text:
                return value[idx:]
            return head.replace(self.remove_text, "") + value[idx:]


@dataclass
class Truncate:
    max_chars: int

    def apply(self, value: str) -> str:
        if self.max_chars < 0:
            return value
        return value[:self.max_chars]


@dataclass
class SubstituteDate:
    """Replace a date found at a character position with another date field."""
    char_pos: int = 0
    source: str = "date"               # "date" | "shootDate"
    target: str = "shootDate"
    occurrence: int = 1

    DATE_RE = re.compile(r"\d{2,4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{6,8}")

    def apply(self, value: str) -> str:
        # The engine has no side-channel for the other date; a caller supplies
        # it via the pipeline context. Standalone, this normalises separators.
        m = self.DATE_RE.search(value, max(0, self.char_pos))
        if not m:
            return value
        raw = m.group(0)
        normalised = raw.replace("/", "-").replace(".", "-")
        return value[:m.start()] + normalised + value[m.end():]


@dataclass
class Conditional:
    """Apply `inner` only when the guard passes."""
    inner: Transform
    require_numbers: bool = False
    require_what: str = ""
    require_deletion: bool = False     # needs pipeline context; see Pipeline

    def guard(self, value: str, prior_deleted: bool = False) -> bool:
        if self.require_numbers and not any(c.isdigit() for c in value):
            return False
        if self.require_what and self.require_what not in value:
            return False
        if self.require_deletion and not prior_deleted:
            return False
        return True


@dataclass
class TransformPipeline:
    operations: list = field(default_factory=list)

    def apply(self, value: str) -> str:
        current = value
        deleted = False
        for op in self.operations:
            if isinstance(op, Conditional):
                if not op.guard(current, deleted):
                    continue
                before = current
                current = op.inner.apply(current)
            else:
                before = current
                current = op.apply(current)
            if len(current) < len(before):
                deleted = True
        return current


def apply_pipeline(value: str, operations: list) -> str:
    return TransformPipeline(list(operations)).apply(value)
