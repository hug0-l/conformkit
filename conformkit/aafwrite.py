"""Binary AAF writer.

Uses pyaaf2 (the independent Python AAF SDK binding) to emit a real
`OLE2`-container AAF that Pro Tools, Nuendo and Resolve can import. The AAFX
writer in `conformkit.aaf` remains dependency-free for XML interchange; this
module is the binary counterpart and is optional.

Structure produced:

    CompositionMob
      TimelineMobSlot per track
        Sequence
          Filler            (gaps)
          SourceClip        (one per event)
            -> SourceMob    (per source file, holds the name)

Requires:  pip install pyaaf2
"""
from __future__ import annotations

import os
from fractions import Fraction

__all__ = ["AafWriteError", "write_binary_aaf", "available"]


class AafWriteError(ValueError):
    pass


def available() -> bool:
    try:
        import aaf2  # noqa: F401
        return True
    except ImportError:
        return False


def _rate_string(rate) -> str:
    """AAF expresses rates as rationals, e.g. '30000/1001'."""
    actual = rate.actual
    if isinstance(actual, Fraction):
        return f"{actual.numerator}/{actual.denominator}"
    return f"{int(actual)}/1"


def _media_kind(track_label: str) -> str:
    return "picture" if track_label.upper().startswith("V") else "sound"


def _rate_string_int(value: int) -> str:
    return f"{int(value)}/1"


def write_binary_aaf(path: str, edit_list, rate,
                     title: str | None = None,
                     sample_rate: int = 48000,
                     trim_ends: bool = True) -> dict:
    """Write an edit list as a binary AAF.

    Returns a summary dict with counts, so a caller can report what was written
    without re-reading the file. `trim_ends` is accepted for API compatibility
    and has no effect: events are written exactly as the list defines them.
    """
    try:
        import aaf2
    except ImportError as exc:                      # pragma: no cover
        raise AafWriteError(
            "binary AAF export needs pyaaf2; install it with "
            "'pip install pyaaf2', or export AAFX instead") from exc

    from .timecode import to_frames

    if rate is None:
        raise AafWriteError("list has no frame rate")

    rate_str = _rate_string(rate)

    # group events per track, preserving record order
    by_track: dict[str, list] = {}
    for ev in sorted(edit_list.events, key=lambda e: to_frames(e.rec_in, rate)):
        for track in ev.tracks:
            by_track.setdefault(str(track), []).append(ev)

    if not by_track:
        raise AafWriteError("list has no tracks")

    written_events: set[int] = set()
    written_clips = 0
    written_tracks = 0

    # A SourceMob must be per (source, media kind): one source name can appear
    # on both a video and an audio track, and a single mob cannot serve both.
    # Its one slot must also span the furthest point any clip reads, otherwise
    # a clip's `start` points past the end of the mob it references.
    extents: dict[tuple[str, str], int] = {}
    for track_label, events in by_track.items():
        kind = _media_kind(track_label)
        for ev in events:
            name = (ev.source_file or ev.clip_name
                    or ev.roll_name or "source")
            start = to_frames(ev.rec_in, rate)
            length = to_frames(ev.rec_out, rate) - start
            if length <= 0:
                continue
            extent = to_frames(ev.src_in, rate) + length
            key = (name, kind)
            extents[key] = max(extents.get(key, 0), extent)

    try:
        with aaf2.open(path, "w") as f:
            comp = f.create.CompositionMob()
            comp.name = title or edit_list.title or "conformkit"
            f.content.mobs.append(comp)

            sources: dict[tuple[str, str], object] = {}

            for slot_index, (track_label, events) in enumerate(
                    sorted(by_track.items()), start=1):
                kind = _media_kind(track_label)
                sequence = f.create.Sequence(media_kind=kind)
                slot = comp.create_timeline_slot(rate_str, slot_index)
                slot.segment = sequence
                slot.name = track_label
                written_tracks += 1

                cursor = 0
                for ev in events:
                    start = to_frames(ev.rec_in, rate)
                    length = to_frames(ev.rec_out, rate) - start
                    if length <= 0:
                        continue

                    gap = start - cursor
                    if gap > 0:
                        sequence.components.append(
                            f.create.Filler(kind, gap))

                    src_name = (ev.source_file or ev.clip_name
                                or ev.roll_name or "source")
                    key = (src_name, kind)
                    src_mob = sources.get(key)
                    if src_mob is None:
                        src_mob = f.create.SourceMob()
                        src_mob.name = src_name
                        # a SourceMob must carry an essence descriptor, even
                        # when no essence is embedded
                        if kind == "sound":
                            desc = f.create.PCMDescriptor()
                            desc['SampleRate'].value = _rate_string_int(sample_rate)
                            desc['Channels'].value = 1
                            desc['AudioSamplingRate'].value = _rate_string_int(
                                sample_rate)
                            # required by the AAF model for a PCM descriptor
                            desc['QuantizationBits'].value = 24
                            desc['BlockAlign'].value = 3      # 1 ch x 24 bit
                            desc['AverageBPS'].value = sample_rate * 3
                        else:
                            desc = f.create.CDCIDescriptor()
                            desc['ComponentWidth'].value = 8
                            desc['StoredWidth'].value = 1920
                            desc['StoredHeight'].value = 1080
                            desc['FrameLayout'].value = 'FullFrame'
                            desc['SampleRate'].value = rate_str
                            # required by the AAF model for a CDCI descriptor
                            desc['HorizontalSubsampling'].value = 2
                            desc['VerticalSubsampling'].value = 1
                            desc['ImageAspectRatio'].value = '16/9'
                            desc['VideoLineMap'].value = [21, 584]
                        desc['Length'].value = extents.get(key, 0)
                        src_mob.descriptor = desc

                        # create the single slot up front, sized to the source
                        # extent, so every clip can reference it
                        if kind == "sound":
                            src_slot = src_mob.create_sound_slot(rate_str)
                        else:
                            src_slot = src_mob.create_picture_slot(rate_str)
                        src_slot.segment.length = extents.get(key, 0)

                        f.content.mobs.append(src_mob)
                        sources[key] = src_mob

                    slot_id = int(src_mob.slots[0].slot_id)
                    clip = f.create.SourceClip(
                        media_kind=kind, length=length,
                        mob_id=src_mob.mob_id, slot_id=slot_id)
                    # SourceClip has no name property; the name lives on the
                    # SourceMob it refers to
                    clip.start = to_frames(ev.src_in, rate)
                    sequence.components.append(clip)
                    written_events.add(id(ev))
                    written_clips += 1
                    cursor = start + length

    except AafWriteError:
        raise
    except Exception as exc:                        # noqa: BLE001
        raise AafWriteError(f"could not write AAF: {exc}") from exc

    return {
        "events": len(written_events),
        "clips": written_clips,
        "tracks": written_tracks,
        "sources": len(sources),
        "size": os.path.getsize(path),
        "rate": rate_str,
    }
