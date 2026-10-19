"""Pro Tools session block tree.

After decryption a session body is a tree of blocks. Each block starts with the
byte 'Z' (0x5A) and has a fixed 7-byte header:

    +0  'Z'           marker
    +1  u16           block type
    +3  u32           block size (payload length, excluding this header)
    +7  u16           content type

Children are laid out inside the parent's payload, so the tree is walked by
recursing at each offset and skipping forward by the child's size when a valid
child is found.

The layout and the type constants follow libptformat (github.com/zamaudio/
ptformat, LGPL-2.1+), which documents the same structure.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

__all__ = [
    "BlockError", "Block", "ZMARK", "BLOCK_HEADER", "HEADER_SIZE", "parse_blocks",
    "walk", "find_blocks", "CONTENT_TYPES", "BlockType",
]

ZMARK = 0x5A
# Header layout: +0 'Z', +1 u16 type, +3 u32 size, +7 u16 content type.
# libptformat sets `offset = pos + 7` and advances by `size + 7`, so the size
# field counts from the content-type field. Keeping the same convention means
# our block boundaries agree with the reference implementation.
BLOCK_HEADER = 7
# Bytes needed before a block can be read: the 7 above plus the content type.
HEADER_SIZE = 9

# Content types observed in session block headers. Names follow libptformat.
CONTENT_TYPES = {
    0x0000: "unknown",
    0x1000: "audio",
    0x1001: "audio-file-list",
    0x1002: "audio-region-list",
    0x1003: "audio-track-list",
    0x1004: "audio-playlist",
    0x1005: "audio-region",
    0x1006: "audio-fade",
    0x2000: "midi",
    0x2001: "midi-region-list",
    0x2002: "midi-track-list",
    0x2003: "midi-region",
    0x3000: "header",
    0x3001: "session-header",
    0x4000: "group",
    0x5000: "marker",
}


class BlockError(ValueError):
    pass


@dataclass
class Block:
    offset: int                 # offset of the 'Z' marker
    block_type: int
    block_size: int             # counts from the content-type field
    content_type: int
    children: list["Block"] = field(default_factory=list)

    @property
    def payload_offset(self) -> int:
        """The content-type field, i.e. libptformat's `block.offset`.

        This is *not* the first data byte. Callers that want the data after the
        content type use `data_offset`; the name is historical.
        """
        return self.offset + BLOCK_HEADER

    @property
    def data_offset(self) -> int:
        """First payload byte after the content-type field."""
        return self.offset + HEADER_SIZE

    @property
    def payload_size(self) -> int:
        """Payload length: `block_size` less the 2-byte content type."""
        return max(0, self.block_size - 2)

    @property
    def end(self) -> int:
        return self.offset + BLOCK_HEADER + self.block_size

    @property
    def content_name(self) -> str:
        return CONTENT_TYPES.get(self.content_type, f"0x{self.content_type:04x}")

    def __repr__(self) -> str:
        return (f"<Block @0x{self.offset:x} type=0x{self.block_type:04x} "
                f"content={self.content_name} size={self.block_size} "
                f"children={len(self.children)}>")


def _read_block(data: bytes, pos: int, limit: int,
                big_endian: bool = False) -> Block | None:
    """Parse one block at `pos`, or return None when there is none."""
    if pos < 0 or pos + HEADER_SIZE > limit or pos + HEADER_SIZE > len(data):
        return None
    if data[pos] != ZMARK:
        return None

    fmt = ">" if big_endian else "<"
    block_type = struct.unpack_from(fmt + "H", data, pos + 1)[0]
    block_size = struct.unpack_from(fmt + "I", data, pos + 3)[0]

    if block_size < 2:
        return None
    if pos + BLOCK_HEADER + block_size > limit:
        return None
    content_type = struct.unpack_from(fmt + "H", data, pos + 7)[0]
    # the top byte of the type is always clear in valid blocks
    if block_type & 0xFF00:
        return None

    return Block(offset=pos, block_type=block_type,
                 block_size=block_size, content_type=content_type)


def _walk_children(data: bytes, parent: Block, limit: int,
                   big_endian: bool, max_depth: int, depth: int) -> None:
    if depth >= max_depth:
        return

    # Children live in the payload, which begins after the content type.
    # Starting at 1 (inside the header) lets a crafted block size invent a
    # phantom child and skip the real first one.
    i = HEADER_SIZE
    while parent.offset + i + HEADER_SIZE <= min(limit, parent.end):
        pos = parent.offset + i
        child = _read_block(data, pos, limit, big_endian)
        if child is None:
            i += 1
            continue
        _walk_children(data, child, min(limit, child.end), big_endian,
                       max_depth, depth + 1)
        parent.children.append(child)
        i = child.end - parent.offset


def parse_blocks(data: bytes, start: int = 0x1F,
                 big_endian: bool = False, max_depth: int = 8,
                 limit: int | None = None) -> list[Block]:
    """Parse the top-level block chain beginning at `start`.

    `data` must already be decrypted. `max_depth` bounds recursion so a corrupt
    size field cannot exhaust the stack.
    """
    if limit is None:
        limit = len(data)
    out: list[Block] = []

    pos = start
    misses = 0
    while pos + HEADER_SIZE <= limit and misses < 1_000_000:
        block = _read_block(data, pos, limit, big_endian)
        if block is None:
            pos += 1
            misses += 1
            continue
        _walk_children(data, block, min(limit, block.end), big_endian,
                       max_depth, 0)
        out.append(block)
        pos = block.end
    return out


def walk(blocks: list[Block], depth: int = 0):
    """Yield (depth, block) for the whole tree, depth-first."""
    for b in blocks:
        yield depth, b
        yield from walk(b.children, depth + 1)


def find_blocks(blocks: list[Block], content_type: int | None = None,
                block_type: int | None = None) -> list[Block]:
    """All blocks matching the given type filters."""
    out = []
    for _, b in walk(blocks):
        if content_type is not None and b.content_type != content_type:
            continue
        if block_type is not None and b.block_type != block_type:
            continue
        out.append(b)
    return out
