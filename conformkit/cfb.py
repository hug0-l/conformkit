"""Minimal MS-CFB (Compound File Binary) reader, per [MS-CFB].

A binary AAF file is a CFB container. Its streams hold SMPTE-keyed objects that
only the AAF SDK can fully interpret, but the container itself is a published
Microsoft format, so listing streams and recovering the embedded names is
straightforward and needs no third-party library.

Malformed input is rejected with `CfbError` rather than being allowed to
allocate without bound: every sector index is range-checked, FAT chains are
cycle-checked, and a short chain is an error instead of a silently truncated
stream.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

__all__ = ["CfbError", "Cfb", "CfbEntry", "CFB_MAGIC"]

CFB_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
SMPTE_UL_PREFIX = bytes.fromhex("060E2B34")

_MAX_CHAIN = 1_000_000
_END_OF_CHAIN = 0xFFFFFFFA


class CfbError(ValueError):
    pass


@dataclass
class CfbEntry:
    index: int
    name: str
    type: int          # 0 empty, 1 storage, 2 stream, 5 root
    start: int
    size: int

    @property
    def kind(self) -> str:
        return {0: "empty", 1: "storage", 2: "stream", 5: "root"}.get(
            self.type, f"type{self.type}")


class Cfb:
    """Read-only view of a compound file."""

    def __init__(self, data: bytes):
        if len(data) < 512 or data[:8] != CFB_MAGIC:
            raise CfbError("not an OLE2 compound file")
        self.data = data
        (self.minor, self.major, self.byte_order, sector_shift,
         mini_shift) = struct.unpack_from("<HHHHH", data, 24)
        if self.byte_order != 0xFFFE:
            raise CfbError("big-endian compound files are not supported")
        # [MS-CFB] only defines 512-byte (v3) and 4096-byte (v4) sectors, and
        # 64-byte mini sectors. Anything else makes the shift arithmetic
        # allocate absurd amounts from a tiny header.
        if sector_shift not in (9, 12):
            raise CfbError(f"unsupported sector shift {sector_shift}")
        if mini_shift != 6:
            raise CfbError(f"unsupported mini sector shift {mini_shift}")
        self.sector_size = 1 << sector_shift
        self.mini_size = 1 << mini_shift
        self.mini_cutoff = struct.unpack_from("<I", data, 56)[0]
        self.mini_fat_start = struct.unpack_from("<I", data, 60)[0]
        self.dir_start = struct.unpack_from("<I", data, 48)[0]
        self.difat_start = struct.unpack_from("<I", data, 68)[0]

        self.max_sectors = max(0, (len(data) - self.sector_size)
                               // self.sector_size + 1)

        self.fat: list[int] = []
        self._load_fat()
        self._entries: list[CfbEntry] | None = None

    # -------------------------------------------------------------- sectors

    def _u32(self, off: int) -> int:
        if off < 0 or off + 4 > len(self.data):
            raise CfbError("header field out of range")
        return struct.unpack_from("<I", self.data, off)[0]

    def _sector(self, n: int) -> bytes:
        if n < 0 or n >= self.max_sectors:
            raise CfbError(f"sector {n} outside the file")
        off = (n + 1) * self.sector_size
        return self.data[off:off + self.sector_size]

    def _full_sector(self, n: int) -> bytes:
        sec = self._sector(n)
        if len(sec) != self.sector_size:
            raise CfbError(f"sector {n} is short ({len(sec)} bytes)")
        return sec

    def _load_fat(self) -> None:
        """Build the FAT by reading the FAT sectors themselves.

        The header's DIFAT list names the sectors that hold the FAT; those
        sectors' contents are the allocation table. Confusing the two yields a
        chain that only appears to work when the indices happen to be small.
        """
        fat_sectors: list[int] = []
        for i in range(109):
            s = self._u32(76 + 4 * i)
            if s < _END_OF_CHAIN:
                fat_sectors.append(s)

        nxt = self.difat_start
        guard = 0
        while nxt < _END_OF_CHAIN and guard < 1000:
            sec = self._full_sector(nxt)
            per = self.sector_size // 4 - 1
            for i in range(per):
                s = struct.unpack_from("<I", sec, 4 * i)[0]
                if s < _END_OF_CHAIN:
                    fat_sectors.append(s)
            nxt = struct.unpack_from("<I", sec, 4 * per)[0]
            guard += 1

        per_sector = self.sector_size // 4
        for s in fat_sectors:
            sec = self._full_sector(s)
            self.fat.extend(struct.unpack_from(f"<{per_sector}I", sec))

    def _chain(self, start: int) -> list[int]:
        out: list[int] = []
        seen: set[int] = set()
        s = start
        while s < _END_OF_CHAIN:
            if s in seen:
                raise CfbError("cyclic FAT chain")
            if len(out) >= _MAX_CHAIN or len(out) >= self.max_sectors:
                raise CfbError("FAT chain longer than the file")
            seen.add(s)
            out.append(s)
            if s >= len(self.fat):
                raise CfbError(f"FAT chain leaves the table at sector {s}")
            s = self.fat[s]
        return out

    # -------------------------------------------------------------- streams

    def directory(self) -> list[CfbEntry]:
        if self._entries is not None:
            return self._entries

        raw = bytearray()
        for s in self._chain(self.dir_start):
            raw += self._full_sector(s)

        entries = []
        for i in range(len(raw) // 128):
            e = raw[i * 128:(i + 1) * 128]
            name_len = struct.unpack_from("<H", e, 64)[0]
            if not (0 < name_len <= 64):
                continue
            entries.append(CfbEntry(
                index=i,
                name=e[0:name_len - 2].decode("utf-16-le", "replace"),
                type=e[66],
                start=struct.unpack_from("<I", e, 116)[0],
                size=struct.unpack_from("<Q", e, 120)[0],
            ))
        self._entries = entries
        return entries

    def streams(self) -> list[CfbEntry]:
        return [e for e in self.directory() if e.type == 2 and e.size]

    def read(self, entry: CfbEntry) -> bytes:
        if entry.size < self.mini_cutoff:
            return self._read_mini(entry.start, entry.size)
        buf = bytearray()
        for s in self._chain(entry.start):
            buf += self._full_sector(s)
            if len(buf) >= entry.size:
                break
        if len(buf) < entry.size:
            raise CfbError(
                f"stream {entry.name!r} declares {entry.size} bytes, "
                f"chain holds {len(buf)}")
        return bytes(buf[:entry.size])

    def _read_mini(self, start: int, size: int) -> bytes:
        mini_fat: list[int] = []
        per = self.sector_size // 4
        for s in self._chain(self.mini_fat_start):
            sec = self._full_sector(s)
            mini_fat += list(struct.unpack_from(f"<{per}I", sec))

        root = next((e for e in self.directory() if e.type == 5), None)
        if root is None:
            raise CfbError("no root entry")
        pool = bytearray()
        for s in self._chain(root.start):
            pool += self._full_sector(s)
        if root.size:
            pool = pool[:root.size]

        out = bytearray()
        seen: set[int] = set()
        s = start
        while s < _END_OF_CHAIN:
            if s in seen:
                raise CfbError("cyclic mini FAT chain")
            if len(out) >= _MAX_CHAIN:
                raise CfbError("mini FAT chain too long")
            seen.add(s)
            chunk = pool[s * self.mini_size:(s + 1) * self.mini_size]
            if not chunk:
                raise CfbError(f"mini sector {s} outside the mini stream")
            out += chunk
            if s >= len(mini_fat):
                break
            s = mini_fat[s]
        if len(out) < size:
            raise CfbError(
                f"mini stream declares {size} bytes, chain holds {len(out)}")
        return bytes(out[:size])
