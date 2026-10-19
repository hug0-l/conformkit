"""WAV / BWF metadata reader.

Parses fmt, data, bext and iXML chunks. Derives a start timecode from the
bext TimeReference and a roll name from iXML TAPE or the bext originator.
"""
from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime

from .timecode import Rate, Timecode, from_frames, seconds_to_frames

__all__ = ["WavInfo", "WavError", "read_wav_info", "derive_start_timecode", "derive_roll_name"]


class WavError(ValueError):
    pass


@dataclass
class WavInfo:
    path: str = ""
    sample_rate: int = 0
    channels: int = 0
    bits_per_sample: int = 0
    duration_frames: int = 0

    # bext
    bext_description: str = ""
    bext_originator: str = ""
    bext_originator_ref: str = ""
    bext_date: str = ""
    bext_time: str = ""
    bext_time_reference: int = 0
    bext_version: int = 0
    bext_coding_history: str = ""

    # iXML
    ix_project: str = ""
    ix_scene: str = ""
    ix_take: str = ""
    ix_tape: str = ""
    ix_note: str = ""
    ix_speed_name: str = ""
    ix_speed_timereference: str = ""
    ix_sync_points: list[tuple[str, str]] = field(default_factory=list)

    # derived
    has_channel_data: bool = False

    @property
    def sample_duration(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return self.duration_frames / self.sample_rate


_KNOWN_CHUNKS = {
    b"fmt ", b"data", b"fact", b"bext", b"iXML", b"LIST", b"cue ", b"smpl",
    b"inst", b"JUNK", b"PAD ", b"minf", b"elm1", b"regn", b"umid", b"axml",
    b"ID3 ", b"id3 ", b"afsp", b"CSET", b"PEAK",
}


def _find_chunks(data: bytes) -> dict[str, bytes]:
    """Walk RIFF chunks.

    Chunks are word-aligned, but files in the wild sometimes omit the pad byte
    on an odd-length chunk. The walker scores the aligned and unaligned
    positions and prefers a known chunk id over a merely printable one: a
    shifted id such as ``ata0`` is printable, so printability alone would skip
    the real next chunk.
    """
    if len(data) < 12 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise WavError("not a RIFF/WAVE file")

    def id_score(off: int) -> int:
        if off + 8 > len(data):
            return 0
        cid = data[off:off + 4]
        if not all((48 <= c <= 57) or (65 <= c <= 90) or (97 <= c <= 122)
                   or c == 32 for c in cid):
            return 0
        size = int.from_bytes(data[off + 4:off + 8], "little")
        if off + 8 + size > len(data) + 1:
            return 0
        return 2 if cid in _KNOWN_CHUNKS else 1

    out: dict[str, bytes] = {}
    off = 12
    n = len(data)
    while off + 8 <= n:
        cid = data[off:off + 4]
        size = struct.unpack_from("<I", data, off + 4)[0]
        body_start = off + 8
        body_end = min(body_start + size, n)

        out.setdefault(cid.decode("ascii", "replace"), data[body_start:body_end])

        aligned = body_start + size + (size & 1)
        unaligned = body_start + size
        if id_score(aligned) >= id_score(unaligned) and id_score(aligned):
            off = aligned
        elif id_score(unaligned):
            off = unaligned
        else:
            off = aligned
    return out


def _parse_bext(b: bytes, info: WavInfo) -> None:
    # A short bext is truncated, not fatal: pad it so the fmt/data chunks are
    # still reported rather than losing the whole file.
    if len(b) < 602:
        b = b.ljust(602, b"\x00")
    info.bext_description = b[0:256].split(b"\x00")[0].decode("ascii", "replace").strip()
    info.bext_originator = b[256:288].split(b"\x00")[0].decode("ascii", "replace").strip()
    info.bext_originator_ref = b[288:320].split(b"\x00")[0].decode("ascii", "replace").strip()
    info.bext_date = b[320:330].split(b"\x00")[0].decode("ascii", "replace").strip()
    info.bext_time = b[330:338].split(b"\x00")[0].decode("ascii", "replace").strip()
    info.bext_time_reference = struct.unpack_from("<Q", b, 338)[0]
    info.bext_version = struct.unpack_from("<H", b, 346)[0]
    if len(b) > 602:
        info.bext_coding_history = b[602:].split(b"\x00")[0].decode("ascii", "replace").strip()


def _parse_ixml(b: bytes, info: WavInfo) -> None:
    text = b.split(b"\x00")[0].decode("utf-8", "replace")
    if not text.strip():
        return
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        # iXML is sometimes not well-formed; leave fields empty rather than fail
        return

    def grab(tag: str) -> str:
        el = root.find(f".//{tag}")
        return (el.text or "").strip() if el is not None else ""

    info.ix_project = grab("PROJECT")
    info.ix_scene = grab("SCENE")
    info.ix_take = grab("TAKE")
    info.ix_tape = grab("TAPE")
    info.ix_note = grab("NOTE")

    sp = root.find(".//SPEED")
    if sp is not None:
        master = sp.find("MASTER_SPEED")
        if master is not None:
            info.ix_speed_name = (master.findtext("MASTER_SPEED_NOTE") or "").strip()
            info.ix_speed_timereference = (master.findtext("TIMECODE_RATE") or "").strip()

    for sp_el in root.findall(".//SYNC_POINT"):
        tc = (sp_el.findtext("SYNC_POINT_TIME") or "").strip()
        nm = (sp_el.findtext("SYNC_POINT_NOTE") or "").strip()
        if tc:
            info.ix_sync_points.append((tc, nm))


def read_wav_info(path: str | None = None, data: bytes | None = None) -> WavInfo:
    if data is None:
        if path is None:
            raise WavError("no data")
        with open(path, "rb") as fh:
            data = fh.read()

    info = WavInfo(path=path or "")
    chunks = _find_chunks(data)

    fmt = chunks.get("fmt ")
    if fmt is None or len(fmt) < 16:
        raise WavError("missing or short fmt chunk")
    audio_format, channels, sample_rate, _, block_align, bits = struct.unpack_from(
        "<HHIIHH", fmt, 0)
    info.channels = channels
    info.sample_rate = sample_rate
    info.bits_per_sample = bits

    dat = chunks.get("data")
    if dat is not None and block_align:
        info.duration_frames = len(dat) // block_align
        info.has_channel_data = info.duration_frames > 0

    if "bext" in chunks:
        _parse_bext(chunks["bext"], info)

    for key in ("iXML", "ixml"):
        if key in chunks:
            _parse_ixml(chunks[key], info)
            break

    return info


# --------------------------------------------------------------- derivations

def derive_start_timecode(info: WavInfo, rate: Rate,
                          session_start: Timecode | None = None) -> Timecode | None:
    """Start timecode from the bext TimeReference.

    TimeReference is a sample offset from midnight (or from the session start
    when one is supplied). Returns None when the file has no bext.
    """
    if info.sample_rate <= 0:
        return None
    if not info.bext_time_reference and not info.bext_date:
        return None

    seconds = info.bext_time_reference / info.sample_rate
    frames = seconds_to_frames(seconds, rate)

    if session_start is not None:
        from .timecode import to_frames
        return from_frames(to_frames(session_start, rate) + frames, rate)
    return from_frames(frames, rate)


def derive_roll_name(info: WavInfo) -> str:
    """Roll name precedence: iXML TAPE, then bext originator."""
    if info.ix_tape:
        return info.ix_tape
    if info.bext_originator:
        return info.bext_originator
    return ""


def derive_scene_take(info: WavInfo) -> tuple[str, str]:
    return info.ix_scene, info.ix_take
