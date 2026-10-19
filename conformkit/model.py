"""Core domain model.

Entities mirror the recovered event field set. Track selection keeps both a
structured set and its EDL text form, and converts losslessly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

from .timecode import Rate, Timecode

__all__ = [
    "Track", "TrackSet", "Transition", "Event", "EditList", "Marker",
    "TRACK_TEXT_RE", "parse_track_text", "format_track_text",
]

TRANSITION_KINDS = {"C": "cut", "D": "dissolve", "W": "wipe", "K": "key"}

# --------------------------------------------------------------------- track

# EDL track column grammar: optional video + optional audio designation,
# combined with '/'. Examples: V, A, A2, AA, B, A2/V, V/A2, AA/V
TRACK_TEXT_RE = re.compile(r"^(?:(?P<vid>V)|(?P<aud>A{1,2}(?:\d+)?|[B-Z]))"
                           r"(?:/(?P<vid2>V)|/(?P<aud2>A{1,2}(?:\d+)?|[B-Z]))?$")


@dataclass(frozen=True, order=True)
class Track:
    kind: str          # "V" or "A"
    label: str         # "V", "A", "A2", "AA", "B"

    def __str__(self) -> str:
        return self.label


@dataclass(frozen=True)
class TrackSet:
    """A set of selected tracks, ordered for stable formatting."""

    tracks: frozenset[Track] = field(default_factory=frozenset)

    def __bool__(self) -> bool:
        return bool(self.tracks)

    def __iter__(self) -> Iterator[Track]:
        return iter(sorted(self.tracks, key=lambda t: (t.kind != "V", t.label)))

    def has_video(self) -> bool:
        return any(t.kind == "V" for t in self.tracks)

    def audio(self) -> list[Track]:
        return [t for t in self if t.kind == "A"]


def parse_track_text(text: str) -> TrackSet:
    """Parse an EDL track column into a TrackSet.

    Accepts 'V', 'A', 'A2', 'AA', 'B', and '/' combinations of one video and
    one audio designation. Raises ValueError with the offending token.
    """
    raw = text.strip()
    if not raw:
        raise ValueError(f"empty track column [{text}]")

    found: set[Track] = set()
    for part in raw.split("/"):
        part = part.strip()
        if not part:
            raise ValueError(f"empty track component in [{text}]")
        if part == "V":
            found.add(Track("V", "V"))
        elif re.fullmatch(r"A{1,2}\d*", part):
            found.add(Track("A", part))
        elif re.fullmatch(r"[B-Z]", part):
            # lettered audio tracks (B, C, ...) used by some GVG variants
            found.add(Track("A", part))
        else:
            raise ValueError(f"unrecognised track component [{part}] in [{text}]")
    if not found:
        raise ValueError(f"no tracks in [{text}]")
    return TrackSet(frozenset(found))


def format_track_text(ts: TrackSet) -> str:
    """Format a TrackSet back to EDL text, video first."""
    if not ts:
        return ""
    return "/".join(str(t) for t in ts)


# ---------------------------------------------------------------- transition

@dataclass(frozen=True)
class Transition:
    kind: str = "C"            # C/D/W/K
    duration: int = 0          # frames; 0 for a cut
    wipe_code: str = ""        # CMX wipe pattern, e.g. "001"

    def __post_init__(self) -> None:
        if self.kind not in TRANSITION_KINDS:
            raise ValueError(f"unknown transition kind [{self.kind}]")
        if self.kind == "C" and self.duration:
            raise ValueError("a cut cannot have a duration")
        if self.kind != "C" and self.duration <= 0:
            raise ValueError("a non-cut transition requires a duration")

    @property
    def name(self) -> str:
        return TRANSITION_KINDS[self.kind]


# --------------------------------------------------------------------- event

@dataclass
class Event:
    rec_in: Timecode
    rec_out: Timecode
    src_in: Timecode
    src_out: Timecode
    roll_name: str
    clip_name: str = ""
    tracks: TrackSet = field(default_factory=TrackSet)
    transition: Transition = field(default_factory=Transition)
    comments: list[str] = field(default_factory=list)
    number: int = 0

    # extended fields
    scene: str = ""
    take: str = ""
    source_file: str = ""
    vfx_update: str = ""
    bad_speed: str = ""
    to_clip_name: str = ""          # "* TO CLIP NAME:", the incoming clip
    effects_name: str = ""          # "EFFECTS NAME IS ..."
    transition_rec_out: Timecode | None = None   # CMX dissolve second line
    annotations: list[str] = field(default_factory=list)  # AUD/M2/>>> lines
    user: dict[str, str] = field(default_factory=dict)

    def rec_duration(self, rate: Rate) -> int:
        from .timecode import sub
        return sub(self.rec_out, self.rec_in, rate)

    def src_duration(self, rate: Rate) -> int:
        from .timecode import sub
        return sub(self.src_out, self.src_in, rate)

    def duration_consistent(self, rate: Rate) -> bool:
        return self.rec_duration(rate) == self.src_duration(rate)


# ----------------------------------------------------------------- edit list

@dataclass
class EditList:
    events: list[Event] = field(default_factory=list)
    rate: Rate | None = None
    title: str = ""
    drop_frame: bool = False
    source_path: str = ""
    remarks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.events)

    def __iter__(self) -> Iterator[Event]:
        return iter(self.events)

    def sorted_by_record(self) -> "EditList":
        return EditList(
            events=sorted(self.events, key=lambda e: e.rec_in),
            rate=self.rate, title=self.title,
            drop_frame=self.drop_frame, source_path=self.source_path,
        )

    def renumber(self) -> None:
        for i, ev in enumerate(self.events, start=1):
            ev.number = i

    def validate(self) -> list[str]:
        """Return a list of problems. Empty list means the list is coherent."""
        from .timecode import TCError

        problems: list[str] = []
        if self.rate is None:
            problems.append("list has no frame rate")
            return problems

        prev = -1
        for i, ev in enumerate(self.events, start=1):
            tag = f"event {i}"
            try:
                if not ev.duration_consistent(self.rate):
                    problems.append(
                        f"{tag}: source duration {ev.src_duration(self.rate)} "
                        f"!= record duration {ev.rec_duration(self.rate)}")
            except TCError as exc:
                problems.append(f"{tag}: {exc}")
                continue

            n = ev.number or i
            if n <= prev:
                problems.append(f"{tag}: event number {n} not monotonic")
            prev = n

            if not ev.roll_name:
                problems.append(f"{tag}: empty roll name")
            if not ev.tracks:
                problems.append(f"{tag}: no track selected")
        return problems


@dataclass
class Marker:
    position: Timecode
    name: str
    comment: str = ""
