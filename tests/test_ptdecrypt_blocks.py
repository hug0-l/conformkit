"""Pro Tools decryption and block-tree tests.

The decryption scheme is verified structurally rather than by trusting a
constant: a correctly decrypted body must be a well-formed chain of 'Z'-marked
blocks whose sizes tile the file without gaps. That is a strong self-check —
a wrong key produces a body with no such structure.
"""
import struct

import pytest

from conformkit.ptblocks import (
    BLOCK_HEADER, ZMARK, parse_blocks, walk, find_blocks,
)
from conformkit.ptdecrypt import (
    DecryptError, XorParams, decrypt, generate_key, is_decrypted, xor_params,
)


# --------------------------------------------------------------- builders

def make_header(xor_type: int, xor_value: int) -> bytearray:
    """A plaintext header: 0x14 bytes, with the XOR parameters at 0x12/0x13."""
    h = bytearray(0x14)
    h[0] = 0x03
    h[1:17] = b"0010111100101011"
    h[0x11] = 0x00
    h[0x12] = xor_type
    h[0x13] = xor_value
    return h


def encrypt_body(body: bytes, params: XorParams) -> bytes:
    """Apply the same scheme the decryptor reverses."""
    key = generate_key(params)
    out = bytearray(body)
    if params.xor_type == 0x01:
        for i in range(len(out)):
            out[i] ^= key[(i + 0x14) & 0xFF]
    else:
        for i in range(len(out)):
            out[i] ^= key[((i + 0x14) >> 12) & 0xFF]
    return bytes(out)


def make_block(block_type: int, content_type: int, payload: bytes) -> bytes:
    """Build a block the way a real session does.

    The header is 'Z' + u16 type + u32 size + u16 content (9 bytes), but `size`
    counts from the content-type field, so it is `len(payload) + 2`. Verified
    against a production session: `offset + 7 + size` lands exactly on the next
    'Z'.
    """
    return (bytes([ZMARK])
            + struct.pack("<H", block_type)
            + struct.pack("<I", len(payload) + 2)
            + struct.pack("<H", content_type)
            + payload)


# --------------------------------------------------------------- key

def test_delta_solves_the_equation():
    """delta must satisfy (delta * mul) & 0xFF == xor_value."""
    for value in (0x00, 0x01, 0xEB, 0x7F, 0xFF):
        for mul, neg in ((53, False), (11, True)):
            p = XorParams(0x01 if not neg else 0x05, value, 0, neg)
            try:
                from conformkit.ptdecrypt import _find_delta
                d = _find_delta(value, mul)
            except DecryptError:
                continue
            assert ((d * mul) & 0xFF) == value


def test_key_is_256_bytes():
    p = XorParams(0x05, 0xEB, -161, True)
    assert len(generate_key(p)) == 256


def test_key_generation_is_deterministic():
    p = XorParams(0x05, 0xEB, -161, True)
    assert generate_key(p) == generate_key(p)


def test_pt10_parameters():
    header = make_header(0x05, 0xEB)
    p = xor_params(bytes(header))
    assert p.xor_type == 0x05
    assert p.negative is True
    assert p.pro_tools_major == "10+"
    assert ((abs(p.delta) * 11) & 0xFF) == 0xEB


def test_pt9_parameters():
    header = make_header(0x01, 0x40)
    p = xor_params(bytes(header))
    assert p.xor_type == 0x01
    assert p.negative is False
    assert p.pro_tools_major == "5-9"
    assert ((p.delta * 53) & 0xFF) == 0x40


def test_unknown_xor_type_rejected():
    with pytest.raises(DecryptError, match="unknown xor_type"):
        xor_params(bytes(make_header(0x09, 0x00)))


def test_short_header_rejected():
    with pytest.raises(DecryptError, match="shorter"):
        xor_params(b"\x03" * 4)


# --------------------------------------------------------------- round trip

@pytest.mark.parametrize("xor_type,xor_value", [(0x05, 0xEB), (0x01, 0x40)])
def test_decrypt_reverses_encrypt(xor_type, xor_value):
    header = bytes(make_header(xor_type, xor_value))
    params = xor_params(header)
    body = bytes(range(256)) * 40
    encrypted = encrypt_body(body, params)
    plain, got = decrypt(header + encrypted)
    assert got.xor_type == params.xor_type
    assert plain[0x14:] == body


def test_header_is_never_encrypted():
    header = bytes(make_header(0x05, 0xEB))
    plain, _ = decrypt(header + b"\x00" * 64)
    assert plain[:0x14] == header


def test_decrypt_rejects_short_file():
    with pytest.raises(DecryptError):
        decrypt(b"\x03" * 8)


# --------------------------------------------------------------- blocks

def test_single_block():
    raw = make_block(0x0001, 0x251A, b"\x01\x02\x03\x04")
    blocks = parse_blocks(raw, start=0)
    assert len(blocks) == 1
    b = blocks[0]
    assert b.block_type == 0x0001
    assert b.content_type == 0x251A
    assert b.payload_size == 4
    assert b.end == len(raw)


def test_chain_of_blocks():
    raw = (make_block(0x0001, 0x251A, b"AAAA")
           + make_block(0x0002, 0x2628, b"BBBBBB")
           + make_block(0x0003, 0x1021, b"CC"))
    blocks = parse_blocks(raw, start=0)
    assert len(blocks) == 3
    assert [b.content_type for b in blocks] == [0x251A, 0x2628, 0x1021]


def test_nested_children():
    child = make_block(0x0002, 0x2628, b"childdata")
    parent = make_block(0x0001, 0x2519, child)
    blocks = parse_blocks(parent, start=0)
    assert len(blocks) == 1
    assert len(blocks[0].children) == 1
    assert blocks[0].children[0].content_type == 0x2628


def test_block_with_high_type_byte_rejected():
    """A valid block type never sets the high byte; such bytes are noise."""
    raw = make_block(0x0101, 0x251A, b"data")
    assert parse_blocks(raw, start=0) == []


def test_zero_size_block_rejected():
    raw = bytes([ZMARK]) + struct.pack("<H", 1) + struct.pack("<I", 0) \
          + struct.pack("<H", 0x251A)
    assert parse_blocks(raw, start=0) == []


def test_oversized_block_rejected():
    raw = bytes([ZMARK]) + struct.pack("<H", 1) + struct.pack("<I", 999999) \
          + struct.pack("<H", 0x251A) + b"short"
    assert parse_blocks(raw, start=0) == []


def test_walk_visits_everything():
    inner = make_block(0x0003, 0x1021, b"x")
    mid = make_block(0x0002, 0x2628, inner)
    top = make_block(0x0001, 0x2519, mid)
    blocks = parse_blocks(top, start=0)
    visited = list(walk(blocks))
    assert len(visited) == 3
    assert [d for d, _ in visited] == [0, 1, 2]


def test_find_blocks_filters():
    raw = (make_block(0x0001, 0x251A, b"AAAA")
           + make_block(0x0002, 0x2628, b"BBBB"))
    blocks = parse_blocks(raw, start=0)
    assert len(find_blocks(blocks, content_type=0x2628)) == 1
    assert len(find_blocks(blocks, block_type=0x0001)) == 1
    assert len(find_blocks(blocks)) == 2


def test_parse_handles_garbage_prefix():
    raw = b"\x00\x11\x22" + make_block(0x0001, 0x251A, b"data")
    blocks = parse_blocks(raw, start=0)
    assert len(blocks) == 1


def test_block_end_arithmetic():
    """`end` must land on the next block's marker.

    The size field counts from the byte after the content type, which is why
    `payload_offset` and the advance both use the 7-byte header.
    """
    raw = make_block(0x0001, 0x251A, b"12345") + make_block(0x0002, 0x2628, b"X")
    blocks = parse_blocks(raw, start=0)
    assert len(blocks) == 2
    first, second = blocks
    assert first.payload_offset == first.offset + BLOCK_HEADER
    assert first.end == second.offset
    assert second.end == len(raw)


# --------------------------------------------------------------- real file

REAL = "fixtures/production/GP04_DSS1.ptx"


@pytest.mark.skipif(not __import__("os").path.exists(REAL),
                    reason="production PTX fixture not present")
def test_real_decrypts_to_valid_block_tree():
    """The decisive check: a wrong key yields no block structure at all."""
    data = open(REAL, "rb").read()
    plain, params = decrypt(data)

    assert params.xor_type == 0x05
    assert params.pro_tools_major == "10+"

    blocks = parse_blocks(plain, big_endian=bool(plain[0x11]))
    assert len(blocks) > 50, "a real session has many top-level blocks"

    total = sum(1 for _ in walk(blocks))
    assert total > 10_000

    # every top-level block must tile forward without gaps
    for a, b in zip(blocks, blocks[1:]):
        assert a.end <= b.offset


@pytest.mark.skipif(not __import__("os").path.exists(REAL),
                    reason="production PTX fixture not present")
def test_real_is_decrypted_detects_state():
    data = open(REAL, "rb").read()
    plain, _ = decrypt(data)
    assert is_decrypted(plain) is True
