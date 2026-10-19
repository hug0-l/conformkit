"""Re-conform: derive the operation sequence that re-cuts a session."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .model import EditList, Event
from .timecode import Rate, sub, to_frames

__all__ = ["OpKind", "ConformOp", "ConformError", "plan_conform", "apply_ops",
           "check_reconformable"]


class ConformError(ValueError):
    pass


class OpKind(str, Enum):
    KEEP = "keep"
    MOVE = "move"
    TRIM = "trim"
    DELETE = "delete"
    INSERT = "insert"


@dataclass
class ConformOp:
    kind: OpKind
    old: Event | None = None
    new: Event | None = None
    delta: int = 0
    detail: str = ""


@dataclass
class ConformPlan:
    ops: list[ConformOp] = field(default_factory=list)
    rate: Rate | None = None

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for o in self.ops:
            out[o.kind.value] = out.get(o.kind.value, 0) + 1
        return out


def check_reconformable(old: EditList, new: EditList,
                        session_start=None,
                        allow_new_material: bool = False) -> list[str]:
    """Return a list of blocking problems. Empty means safe to proceed.

    `allow_new_material` permits events whose roll is absent from the session;
    such events become INSERT operations rather than errors.
    """
    problems: list[str] = []

    if old.rate is None or new.rate is None:
        problems.append("a list has no frame rate")
        return problems
    if old.rate != new.rate:
        problems.append(
            f"frame rates differ: {old.rate.label} vs {new.rate.label}")
        return problems

    if not new.events:
        problems.append("new list is empty")
        return problems

    if session_start is not None:
        earliest = min(new.events, key=lambda e: to_frames(e.rec_in, new.rate))
        if to_frames(earliest.rec_in, new.rate) < to_frames(session_start, new.rate):
            problems.append(
                "new list starts before the session start time")

    old_rolls = {e.roll_name for e in old}
    if not allow_new_material:
        for ev in new.events:
            if not ev.roll_name:
                problems.append("new list contains an event with no roll name")
                break
            if ev.roll_name not in old_rolls:
                problems.append(
                    f"roll {ev.roll_name!r} does not exist in the session")
                break

    for ev in new.events:
        if ev.rec_duration(new.rate) <= 0:
            problems.append("new list contains a zero-length event")
            break

    return problems


def plan_conform(old: EditList, new: EditList,
                 allow_new_material: bool = False) -> ConformPlan:
    """Produce the operation sequence transforming `old` into `new`.

    Matching is by roll name and source range, which is what a session conform
    needs: the material is identified by its source, not its position.
    """
    problems = check_reconformable(old, new,
                                   allow_new_material=allow_new_material)
    if problems:
        raise ConformError("; ".join(problems))

    rate = old.rate
    plan = ConformPlan(rate=rate)

    old_by_src: dict[tuple, Event] = {}
    for ev in old:
        key = (ev.roll_name,
               to_frames(ev.src_in, rate),
               to_frames(ev.src_out, rate))
        old_by_src[key] = ev

    matched_old: set[int] = set()

    for new_ev in sorted(new.events, key=lambda e: to_frames(e.rec_in, rate)):
        key = (new_ev.roll_name,
               to_frames(new_ev.src_in, rate),
               to_frames(new_ev.src_out, rate))
        old_ev = old_by_src.get(key)

        if old_ev is None:
            # try a trim: same roll, overlapping source range
            candidate = None
            for k, e in old_by_src.items():
                if k[0] != new_ev.roll_name:
                    continue
                if k[1] < to_frames(new_ev.src_out, rate) and \
                        k[2] > to_frames(new_ev.src_in, rate):
                    candidate = e
                    break
            if candidate is not None:
                matched_old.add(id(candidate))
                plan.ops.append(ConformOp(
                    OpKind.TRIM, candidate, new_ev,
                    to_frames(new_ev.src_out, rate) - to_frames(candidate.src_out, rate),
                    "source range differs"))
            else:
                plan.ops.append(ConformOp(
                    OpKind.INSERT, None, new_ev, 0, "no source material"))
            continue

        matched_old.add(id(old_ev))
        delta = sub(new_ev.rec_in, old_ev.rec_in, rate)
        if delta == 0:
            plan.ops.append(ConformOp(OpKind.KEEP, old_ev, new_ev))
        else:
            plan.ops.append(ConformOp(OpKind.MOVE, old_ev, new_ev, delta,
                                      f"move {delta:+d} frames"))

    for ev in old:
        if id(ev) not in matched_old:
            plan.ops.append(ConformOp(OpKind.DELETE, ev, None, 0,
                                      "not present in new cut"))

    return plan


def apply_ops(old: EditList, plan: ConformPlan) -> EditList:
    """Apply a conform plan, producing the new list. Used for verification."""
    result = EditList(rate=old.rate, title=old.title)

    for op in plan.ops:
        if op.kind in (OpKind.KEEP, OpKind.MOVE, OpKind.TRIM, OpKind.INSERT):
            ev = op.new
            if ev is None:
                continue
            result.events.append(ev)

    result.events.sort(key=lambda e: to_frames(e.rec_in, result.rate))
    result.renumber()
    return result
