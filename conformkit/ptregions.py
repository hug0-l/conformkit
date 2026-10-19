"""Pro Tools session audio files and regions.

Two things live here:

* **Audio files.** The session lists every source file with its sample rate,
  channel count, bit depth and length in samples. Names come from the
  ``0x103A`` block; the per-file record with the length is the ``0x1001`` block
  nested under ``0x1003`` entries of the same ``0x1004`` list, in the same
  order.

* **Regions.** Each region carries a name, a source file index, and three
  packed integers: ``sample_offset``, ``length`` and ``start``.

The packed integers are **little-endian**, and the field widths come from the
high nibble of three bytes at the record's triple base. Getting the endianness
wrong is invisible until you check the result against the audio: with
big-endian decoding only ~22% of regions fit inside their source file, while
little-endian gives 100%. That check is part of the test suite.

``sample_offset`` and ``length`` are validated: a region must satisfy
``sample_offset + length <= source length``. ``start`` is reported but its
meaning is not established, so it is not used for anything.

Timeline placement lives in a separate map, not on the region record:

    0x1054  region->track full map
      0x1052  one map entry, carrying the track name
        0x1050  one placement
          0x104f  the placement payload

Each ``0x104f`` payload has a region index (u32) at ``+2`` and a 40-bit
position at ``+7``. Byte ``+14`` carries the position unit: bit 6 clear means
the position is in samples, bit 6 set means it is in ticks relative to
``ZERO_TICKS``. Sample positions are validated against the session extent and
against the source order of split regions. Tick positions are reported raw;
converting them to samples needs the session tempo map, which is not decoded.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

__all__ = [
    "RegionError", "AudioFile", "Region", "PlaylistPlacement",
    "extract_audio_files", "extract_regions", "extract_playlists",
    "samples_to_timecode", "REGION_LIST_TYPES", "REGION_TYPES",
    "AUDIO_FILE_LIST_TYPE", "AUDIO_NAME_TYPE", "AUDIO_INFO_TYPE",
    "AUDIO_META_TYPE", "PLAYLIST_MAP_TYPE", "TIMELINE_UNIT_SAMPLES",
    "TIMELINE_UNIT_TICKS", "ZERO_TICKS",
]


class RegionError(ValueError):
    pass


AUDIO_FILE_LIST_TYPE = 0x1004
AUDIO_NAME_TYPE = 0x103A
AUDIO_META_TYPE = 0x1003
AUDIO_INFO_TYPE = 0x1001

REGION_LIST_TYPES = {0x100B, 0x262A}
REGION_TYPES = {0x1008, 0x2629}

# v8/v10 region->track map. 0x1054 is the full map, 0x1052 a per-track map
# entry, 0x1050 a placement, and 0x104f the placement payload.
PLAYLIST_MAP_TYPE = 0x1054
PLAYLIST_TRACK_TYPE = 0x1052
PLAYLIST_ENTRY_TYPE = 0x1050
PLAYLIST_SUBENTRY_TYPE = 0x104F

# Pro Tools stores tick-based positions as a 1e12 offset plus the tick count.
ZERO_TICKS = 0xE8D4A51000

TIMELINE_UNIT_SAMPLES = "samples"
TIMELINE_UNIT_TICKS = "ticks"

MAX_SAMPLES = 48_000 * 60 * 60 * 8


@dataclass
class AudioFile:
    index: int
    name: str = ""
    sample_rate: int = 0
    channels: int = 0
    bits: int = 0
    length: int = 0                 # samples
    block_offset: int = 0

    @property
    def duration_seconds(self) -> float:
        return self.length / self.sample_rate if self.sample_rate else 0.0


@dataclass
class Region:
    name: str
    block_offset: int
    source_index: int = -1
    sample_offset: int = 0
    length: int = 0
    start: int = 0                  # reported, meaning not established
    # None = not checked; True/False = the source-file bound was applied
    reliable: bool | None = None

    @property
    def end(self) -> int:
        return self.sample_offset + self.length

    @property
    def duration_seconds(self) -> float:
        return self.length / 48000.0


@dataclass
class PlaylistPlacement:
    """One placement in the session's region->track map.

    ``position`` is the value as stored: a sample count when ``unit`` is
    ``"samples"``, or an absolute tick count when ``unit`` is ``"ticks"``.
    ``timeline_start`` is only set for sample units, because tick-to-sample
    conversion needs the tempo map. A region can appear more than once (for
    example once per channel of a multichannel track), so placements are a
    list, not a field on `Region`. ``playlist_offset`` identifies the
    ``0x1052`` map entry, since distinct entries can share a track name.
    """
    track_name: str
    region_index: int
    block_offset: int
    position: int
    playlist_offset: int = 0
    unit: str = TIMELINE_UNIT_SAMPLES
    region_name: str = ""
    timeline_start: int | None = None
    timeline_ticks: int | None = None


# --------------------------------------------------------------- primitives

def _u32(data: bytes, off: int, big_endian: bool) -> int:
    if off < 0 or off + 4 > len(data):
        return -1
    return struct.unpack_from(">I" if big_endian else "<I", data, off)[0]


def _u64(data: bytes, off: int, big_endian: bool) -> int:
    if off < 0 or off + 8 > len(data):
        return -1
    return struct.unpack_from(">Q" if big_endian else "<Q", data, off)[0]


def _u40(data: bytes, off: int, big_endian: bool) -> int:
    """A 5-byte integer, as the placement positions are stored."""
    if off < 0 or off + 5 > len(data):
        return -1
    return int.from_bytes(data[off:off + 5], "big" if big_endian else "little")


def _decode_triple(data: bytes, base: int, big_endian: bool = False):
    """Decode the packed (sample_offset, length, start).

    The width-selector bytes are at different positions depending on the
    session's endianness: ``base+1..+3`` for a little-endian session and
    ``base+4..+2`` (reversed) for a big-endian one, matching libptformat. The
    values themselves are always little-endian.
    """
    if base < 0 or base + 5 > len(data):
        return None

    order = (4, 3, 2) if big_endian else (1, 2, 3)
    widths = [(data[base + i] & 0xF0) >> 4 for i in order]
    if any(w > 5 for w in widths) or sum(widths) == 0:
        return None

    values = []
    pos = base + 5
    for w in widths:
        if w == 0:
            values.append(0)
            continue
        if pos + w > len(data):
            return None
        values.append(int.from_bytes(data[pos:pos + w], "little"))
        pos += w
    return values[0], values[1], values[2]


def _region_name(data: bytes, off: int, big_endian: bool):
    n = _u32(data, off, big_endian)
    if not (1 <= n <= 512) or off + 4 + n > len(data):
        return None
    body = data[off + 4:off + 4 + n]
    if b"\x00" in body or not all(32 <= c < 127 for c in body):
        return None
    return body.decode("ascii", "replace")


# --------------------------------------------------------------- audio files

def extract_audio_files(data: bytes, blocks, big_endian: bool) -> list[AudioFile]:
    """Recover the session's source files with their lengths."""
    from .ptblocks import walk

    out: list[AudioFile] = []

    for _, root in walk(blocks):
        if root.content_type != AUDIO_FILE_LIST_TYPE:
            continue

        names: list[str] = []
        for child in root.children:
            if child.content_type != AUDIO_NAME_TYPE:
                continue
            payload = data[child.payload_offset:child.end]
            if len(payload) < 15:
                continue
            count = _u32(data, child.payload_offset + 7, big_endian)
            header_len = _u32(data, child.payload_offset + 11, big_endian)
            pos = 15 + header_len
            # the per-record prefix is a fixed 9 bytes in this generation
            while len(names) < count and pos + 13 <= len(payload):
                n = _u32(payload, pos + 9, big_endian)
                if not (1 <= n <= 512) or pos + 13 + n > len(payload):
                    break
                names.append(
                    payload[pos + 13:pos + 13 + n].decode("utf-8", "replace"))
                pos += 13 + n

        records = []
        for child in root.children:
            if child.content_type != AUDIO_META_TYPE:
                continue
            for grand in child.children:
                if grand.content_type == AUDIO_INFO_TYPE:
                    base = grand.payload_offset
                    if base + 16 > len(data):
                        break
                    records.append((
                        _u32(data, base + 2, big_endian),
                        data[base + 6],
                        data[base + 7],
                        _u64(data, base + 8, big_endian),
                        grand.offset,
                    ))
                    break

        for i, (rate, channels, bits, length, off) in enumerate(records):
            out.append(AudioFile(
                index=i,
                name=names[i] if i < len(names) else "",
                sample_rate=rate, channels=channels, bits=bits,
                length=length, block_offset=off,
            ))
        break
    return out


# --------------------------------------------------------------- regions

def extract_regions(data: bytes, blocks, big_endian: bool,
                    files: list[AudioFile] | None = None) -> list[Region]:
    """Decode every region, validated against the source file when known."""
    from .ptblocks import walk

    out: list[Region] = []

    for _, parent in walk(blocks):
        if parent.content_type not in REGION_LIST_TYPES:
            continue
        for record in parent.children:
            if record.content_type not in REGION_TYPES:
                continue

            name = _region_name(data, record.payload_offset + 11, big_endian)
            if not name:
                continue

            child = record.children[0] if record.children else None
            source_index = (
                _u32(data, child.offset + 7 + child.block_size, big_endian)
                if child else -1)

            decoded = _decode_triple(data, record.payload_offset + 15 + len(name),
                                     big_endian)
            if decoded is None:
                out.append(Region(name=name, block_offset=record.offset,
                                  source_index=source_index))
                continue

            sample_offset, length, start = decoded
            if not (0 < length <= MAX_SAMPLES):
                out.append(Region(name=name, block_offset=record.offset,
                                  source_index=source_index))
                continue

            # Only claim `reliable` when a real source length was available to
            # check against. No source, no verdict.
            reliable: bool | None = None
            if files and 0 <= source_index < len(files):
                source = files[source_index]
                if source.length:
                    reliable = (sample_offset + length <= source.length
                                and sample_offset <= source.length)

            out.append(Region(
                name=name, block_offset=record.offset,
                source_index=source_index,
                sample_offset=sample_offset, length=length, start=start,
                reliable=reliable,
            ))
    return out


# --------------------------------------------------------------- playlists

def extract_playlists(data: bytes, blocks, big_endian: bool,
                      regions: list[Region] | None = None
                      ) -> list[PlaylistPlacement]:
    """Decode the region->track map into timeline placements.

    Walks ``0x1054`` -> ``0x1052`` (track) -> ``0x1050`` -> ``0x104f``. The
    two fields that matter in a ``0x104f`` payload are the region index at
    ``+2`` (u32) and the 40-bit position at ``+7``; byte ``+14`` says whether
    the position is in samples (bit 6 clear) or ticks (bit 6 set).

    The same region can be listed more than once, for example once per channel
    of a multichannel track, so the result is a flat list of placements.
    """
    from .ptblocks import walk

    by_index = {}
    if regions:
        by_index = {i: r for i, r in enumerate(regions)}

    out: list[PlaylistPlacement] = []
    for _, root in walk(blocks):
        if root.content_type != PLAYLIST_MAP_TYPE:
            continue
        for playlist in root.children:
            if playlist.content_type != PLAYLIST_TRACK_TYPE:
                continue
            track_name = _region_name(data, playlist.payload_offset + 2,
                                      big_endian) or ""
            for entry in playlist.children:
                if entry.content_type != PLAYLIST_ENTRY_TYPE:
                    continue
                for sub in entry.children:
                    if sub.content_type != PLAYLIST_SUBENTRY_TYPE:
                        continue

                    base = sub.payload_offset + 2
                    index = _u32(data, base + 2, big_endian)
                    position = _u40(data, base + 7, big_endian)
                    if index < 0 or position < 0:
                        continue

                    flags = data[base + 14] if base + 14 < len(data) else 0
                    if flags & 0x40:
                        unit = TIMELINE_UNIT_TICKS
                        start = None
                        ticks = position - ZERO_TICKS
                    else:
                        unit = TIMELINE_UNIT_SAMPLES
                        start = position
                        ticks = None

                    region = by_index.get(index)
                    out.append(PlaylistPlacement(
                        track_name=track_name,
                        region_index=index,
                        block_offset=sub.offset,
                        position=position,
                        playlist_offset=playlist.offset,
                        unit=unit,
                        region_name=region.name if region else "",
                        timeline_start=start,
                        timeline_ticks=ticks,
                    ))
    return out


def samples_to_timecode(samples: int, sample_rate: int, rate):
    """Convert a sample count to a timecode at the given frame rate."""
    from .timecode import from_frames

    if sample_rate <= 0:
        raise RegionError("sample rate must be positive")
    return from_frames(int(round(samples / sample_rate * float(rate.actual))),
                       rate)
