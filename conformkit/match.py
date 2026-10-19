"""Match / relink: bind edit-list events to audio files."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .bwf import WavInfo, derive_roll_name

__all__ = ["MatchKind", "MatchRecord", "MatchSettings", "match_events", "MatchResult"]


class MatchKind(str, Enum):
    ROLL = "roll"
    FILENAME = "filename"
    BOTH = "both"
    NONE = "none"


@dataclass
class MatchRecord:
    event_ref: object
    wav_ref: WavInfo | None
    kind: MatchKind
    diff_match_and_clip: bool = False
    diff_roll_names: bool = False
    diff_clip_names: bool = False


@dataclass
class MatchSettings:
    by_roll: bool = True
    by_filename: bool = True
    case_sensitive: bool = False
    roll_precedence: bool = True


@dataclass
class MatchResult:
    records: list[MatchRecord] = field(default_factory=list)

    @property
    def matched(self) -> list[MatchRecord]:
        return [r for r in self.records if r.kind != MatchKind.NONE]

    @property
    def unmatched(self) -> list[MatchRecord]:
        return [r for r in self.records if r.kind == MatchKind.NONE]

    def stats(self) -> dict[str, int]:
        out = {"total": len(self.records), "matched": 0, "unmatched": 0}
        for r in self.records:
            if r.kind == MatchKind.NONE:
                out["unmatched"] += 1
            else:
                out["matched"] += 1
        return out


def _norm(s: str, case_sensitive: bool) -> str:
    return s if case_sensitive else s.casefold()


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _stem(name: str) -> str:
    if "." in name:
        return name.rsplit(".", 1)[0]
    return name


def match_events(events, wavs: list[WavInfo],
                 settings: MatchSettings = MatchSettings()) -> MatchResult:
    """Bind each event to a WAV.

    Roll-name match takes precedence when both axes are available and disagree.
    Unmatched events are retained and flagged, never dropped.
    """
    # build lookup tables
    by_roll: dict[str, WavInfo] = {}
    by_name: dict[str, WavInfo] = {}
    for w in wavs:
        roll = derive_roll_name(w)
        if roll:
            by_roll.setdefault(_norm(roll, settings.case_sensitive), w)
        base = _basename(w.path) if w.path else ""
        if base:
            by_name.setdefault(_norm(_stem(base), settings.case_sensitive), w)
            by_name.setdefault(_norm(base, settings.case_sensitive), w)

    result = MatchResult()
    for ev in events:
        roll_hit = None
        name_hit = None

        if settings.by_roll and ev.roll_name:
            roll_hit = by_roll.get(_norm(ev.roll_name, settings.case_sensitive))

        if settings.by_filename:
            probe = ev.source_file or ev.clip_name
            if probe:
                name_hit = (by_name.get(_norm(_stem(_basename(probe)),
                                              settings.case_sensitive))
                            or by_name.get(_norm(_basename(probe),
                                                 settings.case_sensitive)))

        if roll_hit and name_hit:
            if roll_hit is name_hit:
                kind = MatchKind.BOTH
                disagree = False
            else:
                kind = MatchKind.ROLL if settings.roll_precedence else MatchKind.FILENAME
                disagree = True
            rec = MatchRecord(ev, roll_hit if kind != MatchKind.FILENAME else name_hit,
                              kind, diff_roll_names=disagree)
        elif roll_hit:
            rec = MatchRecord(ev, roll_hit, MatchKind.ROLL)
        elif name_hit:
            rec = MatchRecord(ev, name_hit, MatchKind.FILENAME)
        else:
            rec = MatchRecord(ev, None, MatchKind.NONE)

        result.records.append(rec)

    return result
