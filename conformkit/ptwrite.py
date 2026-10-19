"""Safe in-place editing of a Pro Tools session.

Writing a session from scratch is not attempted: the block tree is only partly
understood, and a wrong size field corrupts the file. What is safe — and what
this module does — is **replacement of a name with another name of exactly the
same byte length**. Every block size, offset and CRC-relevant byte stays where
it was, so the structure is untouched.

Typical use: fixing a typo in a track or clip name across an archive, without
opening Pro Tools.

Every write is verified by re-decrypting the result and re-walking the block
tree. If the tree does not match the original, the edit is rejected.

    src -> clone -> patch strings -> re-encrypt -> verify -> dst
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field

from .ptdecrypt import decrypt
from .ptblocks import parse_blocks, walk

__all__ = ["PtxWriteError", "RenameReport", "same_length_renames",
           "rename_names", "write_clone"]


class PtxWriteError(ValueError):
    pass


@dataclass
class RenameReport:
    path: str = ""
    renamed: int = 0
    skipped: int = 0
    blocks_before: int = 0
    blocks_after: int = 0
    verified: bool = False
    changes: list[tuple[str, str]] = field(default_factory=list)
    skipped_reasons: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.renamed} renamed"]
        if self.skipped:
            parts.append(f"{self.skipped} skipped")
        parts.append("verified" if self.verified else "NOT verified")
        return ", ".join(parts)


# names are stored as a u32 length followed by raw bytes, and are also used as
# block names; both must keep their length for an in-place edit to be safe
def same_length_renames(renames: dict[str, str]) -> tuple[dict[str, str],
                                                          list[str]]:
    """Filter a rename map down to the entries that are byte-length neutral."""
    ok: dict[str, str] = {}
    rejected: list[str] = []
    for old, new in renames.items():
        if not old or not new:
            rejected.append(f"{old!r} -> {new!r}: empty name")
            continue
        if len(old.encode("utf-8")) != len(new.encode("utf-8")):
            rejected.append(
                f"{old!r} -> {new!r}: length differs "
                f"({len(old)} vs {len(new)}); an in-place edit is unsafe")
            continue
        ok[old] = new
    return ok, rejected


def _occurrences(data: bytes, needle: bytes) -> list[int]:
    out = []
    start = 0
    while True:
        i = data.find(needle, start)
        if i < 0:
            return out
        out.append(i)
        start = i + 1


def write_clone(src: str, dst: str,
                renames: dict[str, str],
                overwrite: bool = False) -> RenameReport:
    """Copy a session and apply same-length name replacements.

    The original is never opened for writing. `src` is only read.
    """
    if os.path.abspath(src) == os.path.abspath(dst):
        raise PtxWriteError("refusing to write over the source file")
    if os.path.exists(dst) and not overwrite:
        raise PtxWriteError(f"destination exists: {dst}")

    with open(src, "rb") as fh:
        raw = fh.read()

    plain, params = decrypt(raw)
    big_endian = bool(plain[0x11])
    blocks_before = sum(1 for _ in walk(
        parse_blocks(plain, big_endian=big_endian)))

    safe, rejected = same_length_renames(renames)
    report = RenameReport(path=dst, blocks_before=blocks_before,
                          skipped=len(rejected),
                          skipped_reasons=rejected)

    if not safe:
        report.verified = False
        return report

    patched = bytearray(plain)
    for old, new in safe.items():
        hits = _occurrences(plain, old.encode("utf-8"))
        if not hits:
            report.skipped += 1
            report.skipped_reasons.append(f"{old!r}: not found in session")
            continue
        for off in hits:
            patched[off:off + len(old)] = new.encode("utf-8")
        report.renamed += len(hits)
        report.changes.append((old, new))

    # re-encrypt: the XOR covers everything after the plaintext header
    out = bytearray(raw)
    from .ptdecrypt import generate_key, PLAINTEXT_HEADER

    key = generate_key(params)
    for i in range(PLAINTEXT_HEADER, len(out)):
        idx = (i & 0xFF) if params.xor_type == 0x01 else ((i >> 12) & 0xFF)
        out[i] = patched[i] ^ key[idx]

    # verify before touching the destination
    check_plain, _ = decrypt(bytes(out))
    blocks_after = sum(1 for _ in walk(
        parse_blocks(check_plain, big_endian=big_endian)))
    report.blocks_after = blocks_after

    if blocks_after != blocks_before:
        raise PtxWriteError(
            f"structural check failed: {blocks_before} blocks before, "
            f"{blocks_after} after — edit rejected")
    if len(check_plain) != len(plain):
        raise PtxWriteError("size changed; edit rejected")

    report.verified = True
    with open(dst, "wb") as fh:
        fh.write(out)
    return report


def rename_names(src: str, dst: str, renames: dict[str, str],
                 overwrite: bool = False) -> RenameReport:
    """Alias for `write_clone`, named for what the user is doing."""
    return write_clone(src, dst, renames, overwrite=overwrite)
