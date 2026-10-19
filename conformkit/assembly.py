"""Assembly: build a timeline from a folder of location WAV recordings."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .bwf import WavInfo, derive_roll_name, derive_start_timecode, read_wav_info
from .model import EditList, Event, Track, TrackSet, Transition
from .timecode import Rate, Timecode, add, from_frames, to_frames

__all__ = ["AssemblyError", "AssemblyItem", "AssemblyResult", "scan_wavs",
           "build_assembly", "most_common_rate"]


class AssemblyError(ValueError):
    pass


@dataclass
class AssemblyItem:
    wav: WavInfo
    start: Timecode
    duration_frames: int
    scene: str = ""
    take: str = ""
    roll: str = ""
    group: str = ""


@dataclass
class AssemblyResult:
    items: list[AssemblyItem] = field(default_factory=list)
    rate: Rate | None = None
    skipped: list[tuple[str, str]] = field(default_factory=list)

    def to_edit_list(self, title: str = "Assembly") -> EditList:
        if self.rate is None:
            raise AssemblyError("assembly has no rate")
        rate = self.rate
        el = EditList(rate=rate, title=title)

        for i, item in enumerate(sorted(self.items, key=lambda x: x.start), start=1):
            rec_in = item.start
            rec_out = add(rec_in, item.duration_frames, rate)
            src_in = Timecode(0, 0, 0, 0)
            src_out = add(src_in, item.duration_frames, rate)

            tracks = TrackSet(frozenset({Track("A", "A")}))
            if item.wav.channels > 1:
                # poly files occupy one clip with a channel count recorded
                pass

            el.events.append(Event(
                rec_in=rec_in, rec_out=rec_out,
                src_in=src_in, src_out=src_out,
                roll_name=item.roll or derive_roll_name(item.wav),
                clip_name=os.path.basename(item.wav.path) if item.wav.path else "",
                tracks=tracks,
                transition=Transition("C", 0),
                number=i,
                scene=item.scene, take=item.take,
                source_file=item.wav.path,
            ))
        return el


def scan_wavs(folder: str, recursive: bool = True) -> list[str]:
    root = Path(folder)
    if not root.exists():
        raise AssemblyError(f"folder does not exist: {folder}")
    pattern = "**/*.wav" if recursive else "*.wav"
    found = [str(p) for p in root.glob(pattern) if p.is_file()]
    # case-insensitive extension check for platforms that need it
    found += [str(p) for p in root.glob("**/*.WAV" if recursive else "*.WAV")
              if p.is_file()]
    return sorted(set(found))


def most_common_rate(wavs: list[WavInfo]) -> int:
    """The sample rate occurring most often across the given files."""
    counts: dict[int, int] = {}
    for w in wavs:
        if w.sample_rate:
            counts[w.sample_rate] = counts.get(w.sample_rate, 0) + 1
    if not counts:
        return 0
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]


def build_assembly(paths: list[str], rate: Rate,
                   session_start: Timecode | None = None,
                   group_by: str = "scene",
                   warn: list[str] | None = None) -> AssemblyResult:
    """Assemble a timeline from WAV files placed by their bext timestamps.

    Files without a usable timestamp are skipped and reported, never silently
    dropped.
    """
    warnings = warn if warn is not None else []
    result = AssemblyResult(rate=rate)

    for p in paths:
        try:
            info = read_wav_info(path=p)
        except Exception as exc:                      # noqa: BLE001 - report, don't crash
            result.skipped.append((p, f"unreadable: {exc}"))
            continue

        if not info.has_channel_data:
            result.skipped.append((p, "no channel data"))
            continue

        start = derive_start_timecode(info, rate, session_start)
        if start is None:
            result.skipped.append((p, "no bext timestamp"))
            continue

        if info.channels > 1:
            warnings.append(
                f"{os.path.basename(p)}: {info.channels} channels; "
                "consider splitting to mono before AAF export")

        item = AssemblyItem(
            wav=info,
            start=start,
            duration_frames=info.duration_frames,
            scene=info.ix_scene,
            take=info.ix_take,
            roll=derive_roll_name(info),
            group=info.ix_scene if group_by == "scene" else derive_roll_name(info),
        )
        result.items.append(item)

    return result


def resolve_overlaps(result: AssemblyResult) -> None:
    """Push overlapping clips later so nothing overlaps on the single track.

    Deterministic: items are sorted by start, then each is shifted to the end
    of the previous one when it would overlap.
    """
    if result.rate is None:
        raise AssemblyError("assembly has no rate")
    rate = result.rate
    result.items.sort(key=lambda x: (to_frames(x.start, rate), x.wav.path))
    cursor = 0
    for item in result.items:
        start = to_frames(item.start, rate)
        if start < cursor:
            item.start = from_frames(cursor, rate)
            start = cursor
        cursor = start + item.duration_frames
