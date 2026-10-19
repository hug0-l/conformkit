"""Acceptance tests for the timecode engine.

The six vectors are standard SMPTE values and can be verified independently
of any product. If these fail, nothing downstream can be trusted.
"""
import pytest

from conformkit.timecode import (
    TCError, Timecode, add, format_tc, frames_to_seconds, from_frames,
    parse_tc, rate_for, seconds_to_frames, sub, to_frames,
)

DF2997 = rate_for("29.97", df=True)
DF5994 = rate_for("59.94", df=True)
NDF2997 = rate_for("29.97")
R25 = rate_for("25")
R24 = rate_for("24")
R2398 = rate_for("23.976")
R48 = rate_for("48")
R47952 = rate_for("47.952")
R60 = rate_for("60")


# ---------------------------------------------------------------- standards

@pytest.mark.parametrize("text,rate,frames", [
    ("00:00:00:00", R25, 0),
    ("00:01:00;02", DF2997, 1800),
    ("00:00:59;29", DF2997, 1799),
    ("00:10:00;00", DF2997, 17982),
    ("01:00:00:00", R25, 90000),
    ("00:01:00;04", DF5994, 3600),
])
def test_standard_vectors(text, rate, frames):
    tc = parse_tc(text, rate)
    assert to_frames(tc, rate) == frames
    assert from_frames(frames, rate) == tc
    assert format_tc(tc, rate) == text


def test_drop_frame_minute_boundaries():
    """Every minute except every 10th must skip the first two labels.

    Minute m starts at frame 1798*m + 2*ceil(m/10). Verified against the
    standard labels: minute 1 -> 1800 (00:01:00;02), minute 10 -> 17982.
    """
    import math

    for minute in range(0, 20):
        frames = 1798 * minute + 2 * math.ceil(minute / 10)
        tc = from_frames(frames, DF2997)
        assert tc.ss == 0, f"minute {minute} should land on :00 seconds"
        if minute % 10 == 0:
            assert tc.ff == 0, f"minute {minute} should start at frame 00"
        else:
            assert tc.ff == 2, f"minute {minute} should start at frame 02"

    # and the label must parse back to the same frame number
    for minute in range(0, 20):
        ff = 0 if minute % 10 == 0 else 2
        label = f"00:{minute:02d}:00;{ff:02d}"
        frames = 1798 * minute + 2 * math.ceil(minute / 10)
        assert to_frames(parse_tc(label, DF2997), DF2997) == frames


def test_drop_frame_skips_nonexistent_labels():
    for bad in ("00:01:00;00", "00:01:00;01", "00:02:00;00", "00:09:00;01"):
        with pytest.raises(TCError):
            parse_tc(bad, DF2997)
    # every tenth minute is legal
    for good in ("00:10:00;00", "00:20:00;00", "01:00:00;00"):
        parse_tc(good, DF2997)


def test_59_94_drops_four():
    assert to_frames(parse_tc("00:01:00;04", DF5994), DF5994) == 3600
    for bad in ("00:01:00;00", "00:01:00;03"):
        with pytest.raises(TCError):
            parse_tc(bad, DF5994)


# ---------------------------------------------------------------- round trip

@pytest.mark.parametrize("rate", [R2398, R24, R25, DF2997, NDF2997, R47952, R48, DF5994, R60])
def test_roundtrip_sweep(rate):
    for n in range(0, 200_000, 97):
        tc = from_frames(n, rate)
        assert to_frames(tc, rate) == n


@pytest.mark.parametrize("rate", [R2398, R25, DF2997, DF5994])
def test_roundtrip_boundaries(rate):
    boundaries = [0, 1, rate.nominal - 1, rate.nominal,
                  rate.nominal * 60 - 1, rate.nominal * 60,
                  rate.frames_per_10min - 1, rate.frames_per_10min,
                  rate.frames_per_10min * 6]
    for n in boundaries:
        assert to_frames(from_frames(n, rate), rate) == n


# ---------------------------------------------------------------- arithmetic

def test_add_sub():
    a = parse_tc("01:00:00:00", R25)
    b = add(a, 250, R25)
    assert format_tc(b, R25) == "01:00:10:00"
    assert sub(b, a, R25) == 250


def test_negative_sub():
    a = parse_tc("00:00:10:00", R25)
    b = parse_tc("00:00:00:00", R25)
    assert sub(b, a, R25) == -250


def test_add_rolls_hours():
    a = parse_tc("00:59:59:24", R25)
    assert format_tc(add(a, 1, R25), R25) == "01:00:00:00"


# ---------------------------------------------------------------- real time

def test_actual_rate_ntsc():
    # NTSC families are exactly 1000/1001 of nominal, i.e. 30000/1001 for 29.97
    from fractions import Fraction

    assert DF2997.actual == Fraction(30000, 1001)
    assert R2398.actual == Fraction(24000, 1001)
    assert R47952.actual == Fraction(48000, 1001)
    assert float(DF2997.actual) == pytest.approx(29.97002997, abs=1e-8)


def test_seconds_roundtrip():
    for rate in (R25, DF2997, R2398):
        frames = seconds_to_frames(3600.0, rate)
        assert frames_to_seconds(frames, rate) == pytest.approx(3600.0, abs=0.05)


# ---------------------------------------------------------------- errors

@pytest.mark.parametrize("text,rate", [
    ("", R25),
    ("garbage", R25),
    ("00:00:00", R25),
    ("00:00:00:25", R25),
    ("00:60:00:00", R25),
    ("00:00:60:00", R25),
    ("00:00:00;00", R25),      # ';' on a non-drop rate
    ("00:00:00:00", DF2997),   # ':' on a drop rate
])
def test_rejects_bad_input(text, rate):
    with pytest.raises(TCError):
        parse_tc(text, rate)


def test_error_names_the_token():
    with pytest.raises(TCError) as ei:
        parse_tc("00:00:00:99", R25)
    assert "00:00:00:99" in str(ei.value)


def test_negative_frames_rejected():
    with pytest.raises(TCError):
        from_frames(-1, R25)
