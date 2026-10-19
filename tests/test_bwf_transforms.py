"""BWF metadata and column transform tests."""
import struct

import pytest

from conformkit.bwf import (
    WavError, derive_roll_name, derive_start_timecode, read_wav_info,
)
from conformkit.timecode import Timecode, rate_for
from conformkit.transforms import (
    Conditional, InsertText, RemoveChars, RemoveTextAroundAnchor,
    RemoveTextByOccurrence, ReplaceText, TransformPipeline, Truncate,
    apply_pipeline,
)

R25 = rate_for("25")


# --------------------------------------------------------------- WAV builders

def make_wav(channels=2, rate=48000, bits=24, frames=48000,
             bext=None, ixml=None, with_data=True):
    block = channels * (bits // 8)
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * block, block, bits)
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    if bext is not None:
        body += b"bext" + struct.pack("<I", len(bext)) + bext + (b"\x00" if len(bext) % 2 else b"")
    if ixml is not None:
        raw = ixml.encode("utf-8") + b"\x00"
        body += b"iXML" + struct.pack("<I", len(raw)) + raw + (b"\x00" if len(raw) % 2 else b"")
    if with_data:
        data = b"\x00" * (block * frames)
        body += b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


def make_bext(description="", originator="", orig_ref="", date="", time="",
              time_reference=0, version=1, coding=""):
    """Build a 602-byte bext chunk with fixed-width fields.

    Field widths are fixed by EBU Tech 3285: description 256, originator 32,
    originator reference 32, date 10, time 8. Assigning a shorter string must
    not shrink the buffer, so each field is written at its exact width.
    """
    def put(off, width, value, enc="ascii"):
        raw = value.encode(enc)[:width]
        b[off:off + width] = raw.ljust(width, b"\x00")

    b = bytearray(602)
    put(0, 256, description)
    put(256, 32, originator)
    put(288, 32, orig_ref)
    put(320, 10, date)
    put(330, 8, time)
    struct.pack_into("<Q", b, 338, time_reference)
    struct.pack_into("<H", b, 346, version)
    assert len(b) == 602
    return bytes(b) + coding.encode("ascii")


IXML_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<BWFXML>
  <IXML_VERSION>1.5</IXML_VERSION>
  <PROJECT>Mermaids</PROJECT>
  <SCENE>12A</SCENE>
  <TAKE>3</TAKE>
  <TAPE>R0042</TAPE>
  <NOTE>wild track</NOTE>
  <SPEED><MASTER_SPEED><TIMECODE_RATE>25</TIMECODE_RATE></MASTER_SPEED></SPEED>
  <SYNC_POINT>
    <SYNC_POINT_TIME>01:00:00:00</SYNC_POINT_TIME>
    <SYNC_POINT_NOTE>clap</SYNC_POINT_NOTE>
  </SYNC_POINT>
</BWFXML>"""


# --------------------------------------------------------------- WAV tests

def test_reads_fmt_and_data():
    info = read_wav_info(data=make_wav(channels=4, rate=48000, bits=24, frames=24000))
    assert info.channels == 4
    assert info.sample_rate == 48000
    assert info.bits_per_sample == 24
    assert info.duration_frames == 24000
    assert info.has_channel_data is True
    assert info.sample_duration == pytest.approx(0.5)


def test_reads_bext():
    bext = make_bext(description="d1", originator="Sound Devices",
                     orig_ref="A123", date="2024-03-16", time="10:00:00",
                     time_reference=48000, coding="A=PCM")
    info = read_wav_info(data=make_wav(bext=bext))
    assert info.bext_description == "d1"
    assert info.bext_originator == "Sound Devices"
    assert info.bext_originator_ref == "A123"
    assert info.bext_date == "2024-03-16"
    assert info.bext_time_reference == 48000
    assert info.bext_version == 1
    assert info.bext_coding_history == "A=PCM"


def test_reads_ixml():
    info = read_wav_info(data=make_wav(ixml=IXML_SAMPLE))
    assert info.ix_project == "Mermaids"
    assert info.ix_scene == "12A"
    assert info.ix_take == "3"
    assert info.ix_tape == "R0042"
    assert info.ix_note == "wild track"
    assert info.ix_sync_points == [("01:00:00:00", "clap")]


def test_roll_name_precedence():
    info = read_wav_info(data=make_wav(
        bext=make_bext(originator="DevicesOriginator"), ixml=IXML_SAMPLE))
    assert derive_roll_name(info) == "R0042"

    info2 = read_wav_info(data=make_wav(bext=make_bext(originator="DevicesOriginator")))
    assert derive_roll_name(info2) == "DevicesOriginator"


def test_start_timecode_from_bext():
    # 48000 samples at 48k = exactly 1 second = 25 frames at 25fps
    bext = make_bext(date="2024-01-01", time_reference=48000)
    info = read_wav_info(data=make_wav(rate=48000, bext=bext))
    tc = derive_start_timecode(info, R25)
    assert tc == Timecode(0, 0, 1, 0)


def test_start_timecode_one_hour():
    bext = make_bext(date="2024-01-01", time_reference=48000 * 3600)
    info = read_wav_info(data=make_wav(rate=48000, bext=bext))
    assert derive_start_timecode(info, R25) == Timecode(1, 0, 0, 0)


def test_start_timecode_with_session_start():
    bext = make_bext(date="2024-01-01", time_reference=48000 * 10)
    info = read_wav_info(data=make_wav(rate=48000, bext=bext))
    tc = derive_start_timecode(info, R25, session_start=Timecode(1, 0, 0, 0))
    assert tc == Timecode(1, 0, 10, 0)


def test_no_bext_no_timecode():
    info = read_wav_info(data=make_wav())
    assert derive_start_timecode(info, R25) is None


def test_rejects_non_wav():
    with pytest.raises(WavError):
        read_wav_info(data=b"not a wav file at all")


def test_empty_data_chunk_means_no_channel_data():
    info = read_wav_info(data=make_wav(frames=0))
    assert info.has_channel_data is False


# --------------------------------------------------------------- transforms

def test_replace_all():
    assert apply_pipeline("a-b-c", [ReplaceText("-", "_")]) == "a_b_c"


def test_replace_nth():
    assert apply_pipeline("a-b-c", [ReplaceText("-", "_", 1)]) == "a_b-c"
    assert apply_pipeline("a-b-c", [ReplaceText("-", "_", 2)]) == "a-b_c"
    assert apply_pipeline("a-b-c", [ReplaceText("-", "_", 5)]) == "a-b-c"


def test_insert():
    assert apply_pipeline("ABC", [InsertText("XY", 1)]) == "AXYBC"
    assert apply_pipeline("ABC", [InsertText("Z", 0)]) == "ZABC"
    assert apply_pipeline("ABC", [InsertText("Z", 99)]) == "ABCZ"


def test_remove_chars():
    assert apply_pipeline("A1B2C3", [RemoveChars("0123456789")]) == "ABC"
    assert apply_pipeline("a.b,c", [RemoveChars(".,")]) == "abc"


def test_remove_by_occurrence():
    # remove the Nth separator itself
    assert apply_pipeline("X1-X2-X3", [RemoveTextByOccurrence("-", 1)]) == "X1X2-X3"
    assert apply_pipeline("X1-X2-X3", [RemoveTextByOccurrence("-", 2)]) == "X1-X2X3"
    # keep only the segment at the Nth occurrence
    assert apply_pipeline("X1-X2-X3", [RemoveTextByOccurrence("-", 2, False)]) == "X2"
    assert apply_pipeline("X1-X2-X3", [RemoveTextByOccurrence("-", 1, False)]) == "X1"


def test_remove_around_anchor_after():
    assert apply_pipeline("SC12_TAKE3_EXTRA",
                          [RemoveTextAroundAnchor("_", 1, "after")]) == "SC12_"


def test_remove_around_anchor_before():
    assert apply_pipeline("SC12_TAKE3", [RemoveTextAroundAnchor("_", 1, "before")]) == "_TAKE3"


def test_remove_specific_text_after_anchor():
    assert apply_pipeline("SC12_XX_YY",
                          [RemoveTextAroundAnchor("_", 1, "after", "XX")]) == "SC12__YY"


def test_truncate():
    assert apply_pipeline("ABCDEFGH", [Truncate(4)]) == "ABCD"
    assert apply_pipeline("AB", [Truncate(10)]) == "AB"


def test_conditional_requires_numbers():
    op = Conditional(ReplaceText("-", "_"), require_numbers=True)
    assert apply_pipeline("A-1", [op]) == "A_1"
    assert apply_pipeline("A-B", [op]) == "A-B"


def test_conditional_requires_what():
    op = Conditional(Truncate(2), require_what="SC")
    assert apply_pipeline("SC123", [op]) == "SC"
    assert apply_pipeline("XX123", [op]) == "XX123"


def test_pipeline_order_matters():
    ops = [RemoveChars("0123456789"), InsertText("#", 0)]
    assert apply_pipeline("A1B2", ops) == "#AB"


def test_pipeline_is_deterministic():
    ops = [ReplaceText("A", "B"), RemoveChars("xyz"), Truncate(5)]
    first = apply_pipeline("AxAyAz", ops)
    for _ in range(10):
        assert apply_pipeline("AxAyAz", ops) == first


def test_empty_pipeline_is_identity():
    assert apply_pipeline("unchanged", []) == "unchanged"
