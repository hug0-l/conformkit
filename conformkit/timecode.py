"""SMPTE timecode engine.

Supports the rate set recovered from the reference product:
    23.976, 24, 25, 29.97 (DF/NDF), 30, 47.952, 48, 59.94 (DF/NDF), 60, 90

Drop-frame follows SMPTE 12M: 29.97 drops 2 frame numbers per minute except
every 10th; 59.94 drops 4.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

__all__ = ["Rate", "Timecode", "TCError", "RATES", "rate_for", "parse_tc", "format_tc"]

TC_RE = re.compile(r"^\s*(\d{1,3})\s*[:;]\s*(\d{1,2})\s*[:;]\s*(\d{1,2})\s*[:;]\s*(\d{1,3})\s*$")


class TCError(ValueError):
    """Raised for any timecode that cannot be parsed or is out of range."""

    def __init__(self, token: str, reason: str):
        self.token = token
        self.reason = reason
        super().__init__(f"Error reading TC format. [{token}] ({reason})")


@dataclass(frozen=True)
class Rate:
    nominal: int
    df: bool
    label: str

    @property
    def actual(self) -> Fraction:
        """Real-time frame rate (1000/1001 for the NTSC families)."""
        if self.nominal in (24, 30, 60) and self.label in ("23.976", "29.97", "59.94"):
            return Fraction(self.nominal) * Fraction(1000, 1001)
        if self.nominal == 48 and self.label == "47.952":
            return Fraction(48) * Fraction(1000, 1001)
        return Fraction(self.nominal)

    @property
    def drop_frames(self) -> int:
        if not self.df:
            return 0
        if self.nominal == 30:
            return 2
        if self.nominal == 60:
            return 4
        if self.nominal == 120:
            return 8
        raise TCError(self.label, "drop-frame not defined for this rate")

    @property
    def frames_per_10min(self) -> int:
        return self.nominal * 60 * 10 - self.drop_frames * 9

    @property
    def frames_per_min(self) -> int:
        return self.nominal * 60 - self.drop_frames


RATES = {
    "23.976": Rate(24, False, "23.976"),
    "24": Rate(24, False, "24"),
    "25": Rate(25, False, "25"),
    "29.97df": Rate(30, True, "29.97"),
    "29.97": Rate(30, False, "29.97"),
    "30": Rate(30, False, "30"),
    "47.952": Rate(48, False, "47.952"),
    "48": Rate(48, False, "48"),
    "59.94df": Rate(60, True, "59.94"),
    "59.94": Rate(60, False, "59.94"),
    "60": Rate(60, False, "60"),
    "90": Rate(90, False, "90"),
}


def rate_for(label: str, df: bool = False) -> Rate:
    key = f"{label}df" if df and f"{label}df" in RATES else label
    try:
        return RATES[key]
    except KeyError:
        raise TCError(label, "unsupported frame rate") from None


@dataclass(frozen=True, order=True)
class Timecode:
    hh: int
    mm: int
    ss: int
    ff: int

    def __str__(self) -> str:
        return f"{self.hh:02d}:{self.mm:02d}:{self.ss:02d}:{self.ff:02d}"


def _check(tc: Timecode, rate: Rate, token: str) -> Timecode:
    if tc.mm > 59 or tc.ss > 59:
        raise TCError(token, "minutes/seconds out of range")
    if tc.ff >= rate.nominal:
        raise TCError(token, f"frame {tc.ff} exceeds rate {rate.nominal}")
    if rate.df and tc.ss == 0 and tc.mm % 10 != 0 and tc.ff < rate.drop_frames:
        raise TCError(token, "non-existent drop-frame label")
    return tc


def parse_tc(text: str, rate: Rate) -> Timecode:
    m = TC_RE.match(text)
    if not m:
        raise TCError(text, "not a timecode")
    hh, mm, ss, ff = (int(g) for g in m.groups())
    tc = _check(Timecode(hh, mm, ss, ff), rate, text)
    # separator must agree with the rate's drop-frame flag
    sep_df = ";" in text
    if rate.df and not sep_df:
        raise TCError(text, "expected ';' for drop-frame")
    if not rate.df and sep_df:
        raise TCError(text, "unexpected ';' for non-drop")
    return tc


def format_tc(tc: Timecode, rate: Rate) -> str:
    sep = ";" if rate.df else ":"
    return f"{tc.hh:02d}:{tc.mm:02d}:{tc.ss:02d}{sep}{tc.ff:02d}"


def to_frames(tc: Timecode, rate: Rate) -> int:
    total_min = tc.hh * 60 + tc.mm
    base = ((tc.hh * 3600 + tc.mm * 60 + tc.ss) * rate.nominal) + tc.ff
    if rate.df:
        base -= rate.drop_frames * (total_min - total_min // 10)
    return base


def from_frames(frames: int, rate: Rate) -> Timecode:
    if frames < 0:
        raise TCError(str(frames), "negative frame count")
    n = frames
    if rate.df:
        d, m = divmod(n, rate.frames_per_10min)
        if m > rate.drop_frames:
            n += rate.drop_frames * 9 * d + rate.drop_frames * ((m - rate.drop_frames) // rate.frames_per_min)
        else:
            n += rate.drop_frames * 9 * d
    nominal = rate.nominal
    ff = n % nominal
    total_s = n // nominal
    ss = total_s % 60
    total_m = total_s // 60
    mm = total_m % 60
    hh = total_m // 60
    if hh > 99:
        raise TCError(str(frames), "exceeds 99 hours")
    return Timecode(hh, mm, ss, ff)


def add(tc: Timecode, frames: int, rate: Rate) -> Timecode:
    return from_frames(to_frames(tc, rate) + frames, rate)


def sub(a: Timecode, b: Timecode, rate: Rate) -> int:
    return to_frames(a, rate) - to_frames(b, rate)


def seconds_to_frames(seconds: float, rate: Rate) -> int:
    """Convert real-time seconds to a frame count using the true rate."""
    return int(round(seconds * float(rate.actual)))


def frames_to_seconds(frames: int, rate: Rate) -> float:
    return frames / float(rate.actual)
