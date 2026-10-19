"""EDL reader and writer.

Dialects: CMX3600, File16, File32, File129 (Grass Valley), plus an internal
"no format" mode. Roll-name limits differ per dialect.

The parser accepts the real-world CMX3600 dialect as found in production
lists, which is looser than the textbook grammar:

  * zero-length events are permitted (flagged, not rejected)
  * a dissolve/wipe is written as two lines sharing an event number: the first
    is a cut into the transition, the second carries the transition and its
    duration
  * wipes may carry two numeric parameters (wipe pattern + duration)
  * continuation lines that are not events are preserved as annotations:
    "AUD n n", "M2 <roll> <speed> <tc>", "EFFECTS NAME IS ...", ">>> ..."
  * comments may omit the space after the asterisk ("*FROM CLIP NAME:")
  * "* TO CLIP NAME:" names the incoming clip of a transition

Every structural failure raises EDLError naming the offending line and token,
and no partial list is returned.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import EditList, Event, TrackSet, Transition, format_track_text, parse_track_text
from .timecode import Rate, TCError, format_tc, parse_tc, to_frames

__all__ = ["EDLError", "Dialect", "DIALECTS", "parse_edl", "write_edl",
           "detect_dialect", "frame_rate_hint", "EDL_TEXT"]

# --------------------------------------------------------------- dialects

@dataclass(frozen=True)
class Dialect:
    name: str
    roll_limit: int


DIALECTS = {
    "CMX3600": Dialect("CMX3600", 32),
    "File16": Dialect("File16", 16),
    "File32": Dialect("File32", 32),
    "File129": Dialect("File129", 129),
    "none": Dialect("none", 10_000),
}

EDL_TEXT = "EDL"


class EDLError(ValueError):
    def __init__(self, line_no: int, token: str, reason: str):
        self.line_no = line_no
        self.token = token
        self.reason = reason
        super().__init__(f"line {line_no}: {reason} [{token}]")


# --------------------------------------------------------------- grammar

_TC = r"\d{1,3}[:;]\d{1,2}[:;]\d{1,2}[:;]\d{1,3}"

# 001  ROLL  TRACK  C  srcIn srcOut recIn recOut
# 005  ROLL  TRACK  D  070  srcIn srcOut recIn recOut
# 006  ROLL  TRACK  W001 025  srcIn srcOut recIn recOut
_EVENT_RE = re.compile(
    rf"^\s*(?P<num>\d{{1,6}})\s+"
    rf"(?P<roll>\S+)\s+"
    rf"(?P<track>\S+)\s+"
    rf"(?P<trans>[CDWK])(?P<wipe>\d{{0,3}})\s+"
    rf"(?:(?P<dur>\d+)\s+)?"
    rf"(?P<srcin>{_TC})\s+"
    rf"(?P<srcout>{_TC})\s+"
    rf"(?P<recin>{_TC})\s+"
    rf"(?P<recout>{_TC})\s*$"
)

# continuation / annotation lines that carry no event data of their own
_AUD_RE = re.compile(r"^\s*AUD\s+\d+(?:\s+\d+)*\s*$")
_M2_RE = re.compile(r"^\s*M2\s+\S+.*$")
_EFFECTS_RE = re.compile(r"^\s*EFFECTS NAME IS\s+(?P<name>.+?)\s*$")
_ARROW_RE = re.compile(r"^\s*>>>.*$")
_REM_RE = re.compile(r"^\s*REM(?:\s+(?P<text>.*))?$", re.IGNORECASE)

_COMMENT_PREFIXES = [
    ("FROM CLIP NAME", "clip_name"),
    ("TO CLIP NAME", "to_clip_name"),
    ("SOURCE FILE", "source_file"),
    ("SOURCE CUT LIST FILE SELECTED", "note"),
    ("FRAME COUNT OF SOURCE", "note"),
    ("FRAME COUNT OF DESTINATION", "note"),
    ("CREATED USING", "note"),
    ("TEXT ENCODING", "note"),
    ("DATE AND TIME", "note"),
    ("FRMC START", "verbatim"),
    ("ASC_SOP", "verbatim"),
    ("ASC_SAT", "verbatim"),
]


def _split_comment(stripped: str) -> tuple[str, str] | None:
    """Return (upper_prefix, value) for a recognised comment line."""
    body = stripped[1:].lstrip()          # drop '*', tolerate no space
    upper = body.upper()
    for pfx, kind in _COMMENT_PREFIXES:
        if upper.startswith(pfx):
            rest = body[len(pfx):]
            return kind, rest.lstrip(" :\t")
    return None


# --------------------------------------------------------------- parsing

def parse_edl(text: str, rate: Rate, dialect: str = "CMX3600",
              strict_roll_length: bool = True) -> EditList:
    """Parse an EDL document.

    Rate must be supplied by the caller: an EDL declares only drop/non-drop.
    """
    if dialect not in DIALECTS:
        raise EDLError(0, dialect, "unknown dialect")
    limit = DIALECTS[dialect].roll_limit

    el = EditList(rate=rate)
    last_event: Event | None = None
    pair_first: Event | None = None     # first edit of a CMX transition pair
    last_num: int | None = None
    saw_fcm = False

    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\r\n")
        stripped = line.strip()
        if not stripped:
            continue

        upper = stripped.upper()

        # ---- headers
        if upper.startswith("TITLE:"):
            el.title = stripped[6:].strip()
            continue

        if upper.startswith("FCM:"):
            fcm = stripped[4:].strip().upper()
            if fcm not in ("DROP FRAME", "NON-DROP FRAME"):
                raise EDLError(line_no, stripped, "unknown FCM value")
            el.drop_frame = fcm == "DROP FRAME"
            if el.drop_frame != rate.df:
                raise EDLError(
                    line_no, stripped,
                    f"FCM says {'drop' if el.drop_frame else 'non-drop'} "
                    f"but rate is {'drop' if rate.df else 'non-drop'}")
            saw_fcm = True
            continue

        # ---- annotations (no event data)
        if _REM_RE.match(stripped):
            # REM lines are free-text notes, sometimes carrying a licence block
            el.remarks.append(stripped)
            continue

        if _AUD_RE.match(stripped) or _M2_RE.match(stripped) or _ARROW_RE.match(stripped):
            if last_event is not None:
                last_event.annotations.append(stripped)
            continue

        m_eff = _EFFECTS_RE.match(stripped)
        if m_eff:
            if last_event is not None:
                last_event.effects_name = m_eff.group("name")
            continue

        # ---- comments
        if stripped.startswith("*"):
            parsed = _split_comment(stripped)
            if last_event is None:
                continue
            if parsed is None:
                last_event.comments.append(stripped[1:].strip())
                continue
            kind, value = parsed
            if kind == "clip_name":
                # "* FROM CLIP NAME:" names the outgoing clip. In a transition
                # pair written as two lines, that is the first line's edit.
                target = last_event
                if pair_first is not None and pair_first is not last_event:
                    target = pair_first
                target.clip_name = value
            elif kind == "to_clip_name":
                # "* TO CLIP NAME:" names the incoming clip of a transition,
                # i.e. the second line's edit.
                last_event.to_clip_name = value
                last_event.clip_name = value
            elif kind == "source_file":
                last_event.source_file = value
            elif kind == "verbatim":
                # keep the whole line so CDL / FRMC data is not mangled
                last_event.comments.append(stripped[1:].strip())
            else:
                last_event.comments.append(value)
            continue

        # ---- event line
        m = _EVENT_RE.match(line)
        if not m:
            raise EDLError(line_no, stripped, "unrecognised line")

        num = int(m.group("num"))
        roll = m.group("roll")
        track_text = m.group("track")
        trans_kind = m.group("trans")
        wipe_code = m.group("wipe")
        dur_text = m.group("dur")

        if strict_roll_length and len(roll) > limit:
            raise EDLError(
                line_no, roll,
                f"roll name exceeds {limit} chars for {dialect}")

        try:
            tracks = parse_track_text(track_text)
        except ValueError as exc:
            raise EDLError(line_no, track_text, str(exc)) from None

        if trans_kind == "C":
            if dur_text:
                raise EDLError(line_no, stripped, "cut must not carry a duration")
            transition = Transition("C", 0)
        else:
            if not dur_text:
                raise EDLError(line_no, stripped,
                               f"transition {trans_kind} requires a duration")
            dur = int(dur_text)
            if dur <= 0:
                raise EDLError(line_no, dur_text, "transition duration must be positive")
            transition = Transition(trans_kind, dur, wipe_code or "")

        try:
            src_in = parse_tc(m.group("srcin"), rate)
            src_out = parse_tc(m.group("srcout"), rate)
            rec_in = parse_tc(m.group("recin"), rate)
            rec_out = parse_tc(m.group("recout"), rate)
        except TCError as exc:
            raise EDLError(line_no, exc.token, exc.reason) from None

        # A repeated event number is the CMX dissolve convention: two edits
        # share a number, the first being the cut-in point and the second
        # carrying the transition. Both are kept, matching how the format
        # models a transition (pycmx does the same).
        if last_num is not None and num < last_num:
            raise EDLError(
                line_no, str(num),
                f"event number not monotonic (previous {last_num})")

        ev = Event(
            rec_in=rec_in, rec_out=rec_out,
            src_in=src_in, src_out=src_out,
            roll_name=roll, tracks=tracks,
            transition=transition, number=num,
        )

        # Real lists legitimately contain zero-length events (transition
        # anchors) and events whose source and record durations differ
        # (speed changes, freeze frames). These are flagged, never rejected.
        if ev.rec_duration(rate) < 0:
            raise EDLError(line_no, stripped, "record duration is negative")
        if ev.src_duration(rate) < 0:
            raise EDLError(line_no, stripped, "source duration is negative")
        if ev.rec_duration(rate) == 0:
            el.warnings.append(
                f"line {line_no}: event {num} has zero record duration")
        if ev.src_duration(rate) != ev.rec_duration(rate):
            el.warnings.append(
                f"line {line_no}: event {num} source duration "
                f"{ev.src_duration(rate)} != record duration "
                f"{ev.rec_duration(rate)}")

        el.events.append(ev)
        # a repeated number starts/completes a transition pair
        if last_num is not None and num == last_num and last_event is not None:
            pair_first = last_event
        else:
            pair_first = None
        last_event = ev
        last_num = num

    if not saw_fcm and el.events:
        el.drop_frame = rate.df

    # preserve the on-disk numbers exactly; duplicates are meaningful
    return el


# --------------------------------------------------------------- writing

def write_edl(el: EditList, dialect: str = "CMX3600",
              title: str | None = None,
              include_clip_names: bool = True,
              include_source_file: bool = False,
              include_transition_lines: bool = True,
              extra_header: list[str] | None = None) -> str:
    """Serialise an EditList to EDL text."""
    if el.rate is None:
        raise EDLError(0, "", "cannot write an EDL without a frame rate")
    if dialect not in DIALECTS:
        raise EDLError(0, dialect, "unknown dialect")

    rate = el.rate
    limit = DIALECTS[dialect].roll_limit
    out: list[str] = []

    out.append(f"TITLE:   {title if title is not None else el.title}")
    out.append(f"FCM: {'DROP FRAME' if rate.df else 'NON-DROP FRAME'}")
    for h in extra_header or []:
        out.append(h)
    out.append("")

    prev_num = None
    for i, ev in enumerate(el.events, start=1):
        roll = ev.roll_name
        if len(roll) > limit:
            raise EDLError(0, roll, f"roll name exceeds {limit} chars for {dialect}")

        track = format_track_text(ev.tracks)
        if not track:
            raise EDLError(0, str(ev.number or i), "event has no track")

        # preserve the source numbering when it is meaningful (a transition
        # pair shares a number); otherwise number sequentially
        num = ev.number if ev.number else i
        is_pair_second = (prev_num is not None and num == prev_num)

        parts = [f"{num:03d}", roll, track, ev.transition.kind]
        if ev.transition.kind != "C":
            if ev.transition.wipe_code:
                parts[-1] = f"{ev.transition.kind}{ev.transition.wipe_code}"
            parts.append(str(ev.transition.duration))
        parts += [
            format_tc(ev.src_in, rate),
            format_tc(ev.src_out, rate),
            format_tc(ev.rec_in, rate),
            format_tc(ev.rec_out, rate),
        ]
        out.append(" ".join(parts))

        # In a transition pair the first line names the outgoing clip and the
        # second names the incoming clip, so each name is written once.
        if include_clip_names:
            if is_pair_second:
                name = ev.to_clip_name or ev.clip_name
                if name:
                    out.append(f"* TO CLIP NAME: {name}")
            else:
                if ev.clip_name:
                    out.append(f"* FROM CLIP NAME: {ev.clip_name}")
                if ev.to_clip_name:
                    out.append(f"* TO CLIP NAME: {ev.to_clip_name}")
        if include_source_file and ev.source_file:
            out.append(f"* SOURCE FILE: {ev.source_file}")
        if ev.effects_name:
            out.append(f"EFFECTS NAME IS {ev.effects_name}")
        for a in ev.annotations:
            out.append(a)
        for c in ev.comments:
            out.append(f"* {c}")

        prev_num = num

    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------- helpers

def detect_dialect(text: str) -> str:
    longest = 0
    for line in text.splitlines():
        m = _EVENT_RE.match(line)
        if m:
            longest = max(longest, len(m.group("roll")))
    if longest <= 8:
        return "CMX3600"
    if longest <= 16:
        return "File16"
    if longest <= 32:
        return "File32"
    return "File129"


def frame_rate_hint(text: str) -> bool | None:
    for line in text.splitlines():
        u = line.strip().upper()
        if u.startswith("FCM:"):
            v = u[4:].strip()
            if v == "DROP FRAME":
                return True
            if v == "NON-DROP FRAME":
                return False
    return None
