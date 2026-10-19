"""Update Data: apply a versioned change list to an existing list.

The recovered settings surface (UpData*) drives every decision:

    UpDataUpdateTCsIn          which columns carry timecodes
    UpDataUpdateVersionField   whether to stamp a version field
    UpDataUpdateVersionFieldName / With
    UpDataTCFormat             timecode format for the update
    UpDataTCUpdateDels         delete entries carrying this tag
    UpDataTCUpdateDelTag
    UpDataNonUpTCReplaceTC / ReplaceWith   fallback for un-updatable timecodes
    UpDataNonUpTCAddAfterIt / AddAfterStr
    UpDataCreateColCellData / UpDataCreateColHeaderName

Semantics: a change list produced against version N is applied to a list at
version N, moving events to their new positions. Anything the change list does
not mention is left alone. Application is transactional — on error the list is
untouched.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["UpdateSettings", "UpdateResult", "UpdateError", "apply_update",
           "ChangeListEntry"]


class UpdateError(ValueError):
    pass


@dataclass
class UpdateSettings:
    """Mirrors the persisted UpData* keys."""

    update_timecodes_in: tuple[str, ...] = ("destination",)
    update_version_field: bool = False
    version_field_name: str = "Version"
    version_field_value: str = ""
    delete_tagged: bool = False
    delete_tag: str = ""
    replace_non_updatable: bool = False
    replace_non_updatable_with: str = ""
    add_after_non_updatable: bool = False
    add_after_string: str = ""
    create_column_cell_data: bool = False
    create_column_header_name: str = ""


@dataclass
class ChangeListEntry:
    """One row of a versioned change list."""

    event_number: int = 0
    roll: str = ""
    new_rec_in: object | None = None     # Timecode
    new_rec_out: object | None = None
    new_src_in: object | None = None
    new_src_out: object | None = None
    tag: str = ""
    note: str = ""


@dataclass
class UpdateResult:
    applied: int = 0
    skipped: int = 0
    deleted: int = 0
    replaced: int = 0
    added_after: int = 0
    version_stamped: bool = False
    messages: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.applied} updated"]
        if self.deleted:
            parts.append(f"{self.deleted} deleted")
        if self.skipped:
            parts.append(f"{self.skipped} skipped")
        if self.replaced:
            parts.append(f"{self.replaced} replaced")
        if self.added_after:
            parts.append(f"{self.added_after} annotated")
        if self.version_stamped:
            parts.append("version stamped")
        return ", ".join(parts)


def _match(ev, entry: ChangeListEntry) -> bool:
    """Match by event number when the entry has one, else by roll.

    The two are alternatives, not a union: a list may contain many events on
    the same roll, so matching on roll as well would let one entry apply to
    every one of them.
    """
    if entry.event_number:
        return ev.number == entry.event_number
    if entry.roll:
        return ev.roll_name == entry.roll
    return False


def apply_update(edit_list, rate, entries: list[ChangeListEntry],
                 settings: UpdateSettings | None = None) -> UpdateResult:
    """Apply a change list. Transactional: the list is replaced only on success."""
    settings = settings or UpdateSettings()
    result = UpdateResult()

    # a change list addresses events by number, so numbering must be current
    if any(not ev.number for ev in edit_list.events):
        edit_list.renumber()

    # work on copies so a failure cannot leave half-applied state: mutating the
    # events in place would leak through even if the list assignment never runs
    import copy

    original = edit_list.events
    working = [copy.deepcopy(ev) for ev in original]

    try:
        for entry in entries:
            if settings.delete_tagged and entry.tag and \
                    entry.tag == settings.delete_tag:
                kept = []
                for ev in working:
                    if _match(ev, entry):
                        result.deleted += 1
                    else:
                        kept.append(ev)
                working = kept
                continue

            target = None
            for ev in working:
                if _match(ev, entry):
                    target = ev
                    break

            if target is None:
                result.skipped += 1
                result.messages.append(
                    f"no event matches {entry.event_number or entry.roll}")
                continue

            changed = False
            if "destination" in settings.update_timecodes_in:
                if entry.new_rec_in is not None and entry.new_rec_out is not None:
                    target.rec_in = entry.new_rec_in
                    target.rec_out = entry.new_rec_out
                    changed = True
            if "source" in settings.update_timecodes_in:
                if entry.new_src_in is not None and entry.new_src_out is not None:
                    target.src_in = entry.new_src_in
                    target.src_out = entry.new_src_out
                    changed = True

            if changed:
                result.applied += 1
            else:
                # no usable timecodes: fall back per settings
                if settings.replace_non_updatable and settings.replace_non_updatable_with:
                    target.comments.append(settings.replace_non_updatable_with)
                    result.replaced += 1
                elif settings.add_after_non_updatable and settings.add_after_string:
                    target.comments.append(settings.add_after_string)
                    result.added_after += 1
                else:
                    result.skipped += 1

        if settings.create_column_cell_data and settings.create_column_header_name:
            for ev in working:
                ev.user.setdefault(settings.create_column_header_name, "")

        if settings.update_version_field and settings.version_field_name:
            for ev in working:
                ev.user[settings.version_field_name] = settings.version_field_value
            result.version_stamped = True

    except Exception as exc:                        # noqa: BLE001
        raise UpdateError(str(exc)) from exc

    # commit
    edit_list.events[:] = working
    edit_list.renumber()
    return result


def parse_change_list(text: str, rate) -> list[ChangeListEntry]:
    """Read a change list as tab-delimited rows.

    Expected columns (header row, any order):
        EVENT  ROLL  REC_IN  REC_OUT  SRC_IN  SRC_OUT  TAG  NOTE
    """
    from .timecode import parse_tc

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []

    header = [h.strip().upper() for h in lines[0].split("\t")]
    index = {name: i for i, name in enumerate(header)}

    entries: list[ChangeListEntry] = []
    for line in lines[1:]:
        cells = line.split("\t")
        def get(name: str) -> str:
            i = index.get(name)
            return cells[i].strip() if i is not None and i < len(cells) else ""

        entry = ChangeListEntry(
            event_number=int(get("EVENT") or 0),
            roll=get("ROLL"),
            tag=get("TAG"),
            note=get("NOTE"),
        )
        for attr, col in (("new_rec_in", "REC_IN"), ("new_rec_out", "REC_OUT"),
                          ("new_src_in", "SRC_IN"), ("new_src_out", "SRC_OUT")):
            value = get(col)
            if value:
                try:
                    setattr(entry, attr, parse_tc(value, rate))
                except Exception:                   # noqa: BLE001
                    pass
        entries.append(entry)
    return entries
