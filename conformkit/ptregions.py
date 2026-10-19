"""Pro Tools session regions.

A session stores audio regions (clips): each has a name, a source file, and a
position and length within that source. Names and source indices decode
reliably. The timing triple does not, for every record, because some sessions
use a different record layout that could not be pinned down.

Rather than guess, this module decodes every plausible interpretation and
reports a **confidence** per region:

    high   the encoding is unambiguous, or the per-source file-length bound
           eliminated every other candidate
    low    more than one interpretation survived; a value is reported but
           should not be trusted for automated conform work
    none   no interpretation survived; name and source index are still valid

The disambiguation is principled, not heuristic:

1. **Minimal encoding.** Packed integers are written without redundant leading
   zero bytes, so a candidate whose field starts with 0x00 is wrong.
2. **File bound.** A region's `[start, start+length)` must lie inside its source
   file. Bounds are bootstrapped from the unambiguous records, then used to
   reject candidates that overrun.

This is read-only. Nothing here writes to a session.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

__all__ = [
    "RegionError", "Region", "PlaylistEntry", "extract_regions",
    "extract_playlists", "samples_to_timecode", "REGION_LIST_TYPES",
    "REGION_TYPES", "CONFIDENCE_HIGH", "CONFIDENCE_LOW", "CONFIDENCE_NONE",
]


class RegionError(ValueError):
    pass


REGION_LIST_TYPES = {0x100B, 0x262A}
REGION_TYPES = {0x1008, 0x2629}
PLAYLIST_TYPE = 0x1004

CONFIDENCE_HIGH = "high"
CONFIDENCE_LOW = "low"
CONFIDENCE_NONE = "none"

# A sample count above this is a misparse, not a real recording: 48 kHz for 8 h.
MAX_SAMPLES = 48_000 * 60 * 60 * 8

# Offsets the triple has been observed at, relative to the end of the name.
# 0 is the documented position; the others cover records with extra fields.
_CANDIDATE_DELTAS = (0, -1, -2, -3, -4, 1, 2, 3)


@dataclass
class Region:
    name: str
    block_offset: int
    source_index: int = -1
    sample_offset: int = 0
    length: int = 0
    start: int = 0
    confidence: str = CONFIDENCE_NONE

    @property
    def reliable(self) -> bool:
        return self.confidence == CONFIDENCE_HIGH

    @property
    def end(self) -> int:
        return self.start + self.length

    @property
    def duration_seconds(self) -> float:
        return self.length / 48000.0


@dataclass
class PlaylistEntry:
    track_index: int
    region_index: int
    block_offset: int


# --------------------------------------------------------------- primitives

def _u32(data: bytes, off: int, big_endian: bool) -> int:
    if off < 0 or off + 4 > len(data):
        return -1
    return struct.unpack_from(">I" if big_endian else "<I", data, off)[0]


def _name_at(data: bytes, off: int, big_endian: bool):
    n = _u32(data, off, big_endian)
    if not (1 <= n <= 512) or off + 4 + n > len(data):
        return None
    body = data[off + 4:off + 4 + n]
    if b"\x00" in body or not all(32 <= c < 127 for c in body):
        return None
    return body.decode("ascii", "replace")


def _decode_at(data: bytes, base: int):
    """Decode the triple at `base`.

    Returns (sample_offset, length, start, minimal) or None.
    """
    if base < 0 or base + 5 > len(data):
        return None

    widths = [(data[base + i] & 0xF0) >> 4 for i in (1, 2, 3)]
    if any(w > 5 for w in widths) or sum(widths) == 0:
        return None

    values = []
    minimal = True
    pos = base + 5
    for w in widths:
        if w == 0:
            values.append(0)
            continue
        if pos + w > len(data):
            return None
        raw = data[pos:pos + w]
        if raw[0] == 0:                 # redundant leading zero: wrong base
            minimal = False
        values.append(int.from_bytes(raw, "big"))
        pos += w

    return values[0], values[1], values[2], minimal


def _plausible(sample_offset: int, length: int, start: int) -> bool:
    return (0 < length <= MAX_SAMPLES
            and 0 <= start <= MAX_SAMPLES
            and 0 <= sample_offset <= MAX_SAMPLES)


# --------------------------------------------------------------- regions

def extract_regions(data: bytes, blocks, big_endian: bool) -> list[Region]:
    """Decode every region record, with a confidence rating per record.

    Every region with a readable name is returned. Records whose timing cannot
    be established are returned with `confidence="none"`, so the name and
    source index are never lost.
    """
    from .ptblocks import walk

    raw: list[tuple[Region, list]] = []

    for _, parent in walk(blocks):
        if parent.content_type not in REGION_LIST_TYPES:
            continue
        for region in parent.children:
            if region.content_type not in REGION_TYPES:
                continue

            name = _name_at(data, region.payload_offset + 11, big_endian)
            if not name:
                continue

            child = region.children[0] if region.children else None
            source_index = (
                _u32(data, child.offset + 7 + child.block_size, big_endian)
                if child else -1)

            base = region.payload_offset + 15 + len(name)
            candidates = []
            for delta in _CANDIDATE_DELTAS:
                decoded = _decode_at(data, base + delta)
                if decoded is None:
                    continue
                so, ln, st, minimal = decoded
                if minimal and _plausible(so, ln, st):
                    candidates.append((so, ln, st))

            raw.append((Region(name=name, block_offset=region.offset,
                               source_index=source_index), candidates))

    # --- bootstrap a file-length bound per source from unambiguous records
    bounds: dict[int, int] = {}
    for region, candidates in raw:
        if len(candidates) == 1:
            _, length, start = candidates[0]
            bounds[region.source_index] = max(
                bounds.get(region.source_index, 0), start + length)

    # --- resolve
    out: list[Region] = []
    for region, candidates in raw:
        if not candidates:
            out.append(region)
            continue

        if len(candidates) == 1:
            so, ln, st = candidates[0]
            region.sample_offset, region.length, region.start = so, ln, st
            region.confidence = CONFIDENCE_HIGH
            out.append(region)
            continue

        bound = bounds.get(region.source_index, 0)
        if bound:
            fits = [c for c in candidates if c[2] + c[1] <= bound * 1.02]
            if len(fits) == 1:
                so, ln, st = fits[0]
                region.sample_offset, region.length, region.start = so, ln, st
                region.confidence = CONFIDENCE_HIGH
                out.append(region)
                continue
            if fits:
                candidates = fits

        so, ln, st = candidates[0]
        region.sample_offset, region.length, region.start = so, ln, st
        region.confidence = CONFIDENCE_LOW
        out.append(region)

    return out


# --------------------------------------------------------------- playlists

def extract_playlists(data: bytes, blocks, big_endian: bool) -> list[PlaylistEntry]:
    """Decode playlist entries: which region sits on which track slot."""
    from .ptblocks import walk

    out: list[PlaylistEntry] = []
    for _, playlist in walk(blocks):
        if playlist.content_type != PLAYLIST_TYPE:
            continue
        for slot_index, slot in enumerate(playlist.children):
            if slot.content_type != 0x1003:
                continue
            for entry in slot.children:
                idx = _u32(data, entry.payload_offset + 2, big_endian)
                if 0 <= idx < 1_000_000:
                    out.append(PlaylistEntry(track_index=slot_index,
                                             region_index=idx,
                                             block_offset=entry.offset))
    return out


def samples_to_timecode(samples: int, sample_rate: int, rate):
    """Convert a sample count to a timecode at the given frame rate."""
    from .timecode import from_frames

    if sample_rate <= 0:
        raise RegionError("sample rate must be positive")
    return from_frames(int(round(samples / sample_rate * float(rate.actual))),
                       rate)
