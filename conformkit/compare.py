"""Compare two edit lists and produce a change list."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .model import EditList, Event
from .timecode import Rate, sub

__all__ = ["ChangeType", "Change", "ChangeList", "CompareSettings", "compare_lists",
           "DEFAULT_SETTINGS", "ALL_OFF_SETTINGS"]


class ChangeType(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    MOVED = "moved"
    TRIMMED = "trimmed"
    UNCHANGED = "unchanged"


@dataclass
class Change:
    change_type: ChangeType
    old: Event | None = None
    new: Event | None = None
    delta: int = 0
    detail: str = ""


@dataclass
class ChangeList:
    changes: list[Change] = field(default_factory=list)
    old_title: str = ""
    new_title: str = ""
    rate: Rate | None = None

    def __len__(self) -> int:
        return len(self.changes)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.changes:
            out[c.change_type.value] = out.get(c.change_type.value, 0) + 1
        return out

    def is_noisy(self, threshold: float = 0.5) -> bool:
        """True when most changes are +/-1 frame, suggesting an offset issue."""
        moves = [c for c in self.changes if c.change_type == ChangeType.MOVED]
        if not moves:
            return False
        small = sum(1 for c in moves if abs(c.delta) <= 1)
        return small / len(moves) >= threshold

    def single_track(self) -> bool:
        tracks = set()
        for c in self.changes:
            for ev in (c.old, c.new):
                if ev is not None:
                    tracks |= set(ev.tracks.tracks)
        return len(tracks) <= 1


@dataclass
class CompareSettings:
    match_roll: bool = True
    match_clip: bool = True
    match_record_position: bool = True
    tolerance_frames: int = 0
    report_unchanged: bool = False
    report_deletions: bool = True
    report_gaps: bool = False
    treat_move_as_new: bool = False


DEFAULT_SETTINGS = CompareSettings()

ALL_OFF_SETTINGS = CompareSettings(
    match_roll=False, match_clip=False, match_record_position=False,
    tolerance_frames=0, report_unchanged=False,
    report_deletions=False, report_gaps=False,
)


def _key(ev: Event, settings: CompareSettings) -> tuple:
    """Identity key for matching. When no axis is enabled, nothing can match,
    so the key is unique per event rather than an empty tuple."""
    parts = []
    if settings.match_roll:
        parts.append(ev.roll_name.casefold())
    if settings.match_clip:
        parts.append(ev.clip_name.casefold())
    if not parts:
        return ("__unmatchable__", id(ev))
    return tuple(parts)


def compare_lists(old: EditList, new: EditList,
                  settings: CompareSettings = DEFAULT_SETTINGS) -> ChangeList:
    """Diff two lists. Deterministic for identical inputs."""
    if old.rate is None or new.rate is None:
        raise ValueError("both lists need a frame rate")
    if old.rate != new.rate:
        raise ValueError(
            f"frame rate mismatch: {old.rate.label} vs {new.rate.label}")

    rate = old.rate
    result = ChangeList(old_title=old.title, new_title=new.title, rate=rate)

    # index the old list by key; multiple events may share a key
    old_index: dict[tuple, list[Event]] = {}
    for ev in old:
        old_index.setdefault(_key(ev, settings), []).append(ev)

    consumed: set[int] = set()

    for new_ev in new:
        k = _key(new_ev, settings)
        candidates = [e for e in old_index.get(k, []) if id(e) not in consumed]

        if not candidates:
            result.changes.append(Change(ChangeType.ADDED, None, new_ev,
                                         detail="no match in old list"))
            continue

        # pick the candidate whose record-in is nearest
        def distance(e: Event) -> int:
            return abs(sub(new_ev.rec_in, e.rec_in, rate))

        best = min(candidates, key=distance)
        consumed.add(id(best))

        rec_delta = sub(new_ev.rec_in, best.rec_in, rate)
        old_dur = best.rec_duration(rate)
        new_dur = new_ev.rec_duration(rate)
        dur_delta = new_dur - old_dur

        if rec_delta == 0 and abs(dur_delta) <= settings.tolerance_frames:
            if settings.report_unchanged:
                result.changes.append(Change(ChangeType.UNCHANGED, best, new_ev))
            continue

        if settings.treat_move_as_new and rec_delta != 0:
            result.changes.append(Change(ChangeType.REMOVED, best, None,
                                         detail="treated as new"))
            result.changes.append(Change(ChangeType.ADDED, None, new_ev,
                                         detail="treated as new"))
            continue

        if rec_delta != 0:
            result.changes.append(
                Change(ChangeType.MOVED, best, new_ev, rec_delta,
                       f"record-in moved {rec_delta:+d} frames"))
        if abs(dur_delta) > settings.tolerance_frames:
            result.changes.append(
                Change(ChangeType.TRIMMED, best, new_ev, dur_delta,
                       f"duration {dur_delta:+d} frames"))

    if settings.report_deletions:
        for ev in old:
            if id(ev) not in consumed:
                result.changes.append(Change(ChangeType.REMOVED, ev, None,
                                             detail="absent from new list"))

    return result
