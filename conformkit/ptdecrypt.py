"""Pro Tools session decryption.

Pro Tools obfuscates the body of a session with a repeating XOR key. The first
0x14 bytes are always plaintext; everything after that is XORed against a
256-byte key table.

The key table is not a constant. It is generated from two bytes in the header:

    offset 0x12 : xor_type    0x01 = Pro Tools 5-9, 0x05 = Pro Tools 10+
    offset 0x13 : xor_value   seeds the delta

    delta = the i in 0..255 where (i * mul) & 0xFF == xor_value
    mul   = 53 and delta positive for xor_type 0x01
            11 and delta negative for xor_type 0x05

    key[i] = (i * delta) & 0xFF

The index into the key differs by version:

    xor_type 0x01 : key_index = file_offset & 0xFF
    xor_type 0x05 : key_index = (file_offset >> 12) & 0xFF

The latter means the key only changes every 4096 bytes, which is why the
plaintext/obfuscated regions of a PTX appear to alternate in 4 KiB blocks.

This scheme was established by libptformat (github.com/zamaudio/ptformat,
LGPL-2.1+), whose header exposes the same block layout and XOR parameters.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "DecryptError", "XorParams", "xor_params", "generate_key",
    "decrypt", "decrypt_file", "is_decrypted", "PLAINTEXT_HEADER",
]

PLAINTEXT_HEADER = 0x14

XOR_TYPE_PT59 = 0x01
XOR_TYPE_PT10 = 0x05


class DecryptError(ValueError):
    pass


@dataclass(frozen=True)
class XorParams:
    xor_type: int
    xor_value: int
    delta: int
    negative: bool

    @property
    def pro_tools_major(self) -> str:
        if self.xor_type == XOR_TYPE_PT59:
            return "5-9"
        if self.xor_type == XOR_TYPE_PT10:
            return "10+"
        return "unknown"


def _find_delta(xor_value: int, mul: int) -> int:
    """Solve (i * mul) & 0xFF == xor_value for i in 0..255."""
    for i in range(256):
        if ((i * mul) & 0xFF) == xor_value:
            return i
    raise DecryptError(
        f"no delta satisfies (i * {mul}) & 0xFF == 0x{xor_value:02x}")


def xor_params(header: bytes) -> XorParams:
    """Read the XOR parameters from the plaintext header."""
    if len(header) < PLAINTEXT_HEADER:
        raise DecryptError("header shorter than 0x14 bytes")

    xor_type = header[0x12]
    xor_value = header[0x13]

    if xor_type == XOR_TYPE_PT59:
        delta = _find_delta(xor_value, 53)
        return XorParams(xor_type, xor_value, delta, False)
    if xor_type == XOR_TYPE_PT10:
        delta = _find_delta(xor_value, 11)
        return XorParams(xor_type, xor_value, -delta, True)

    raise DecryptError(f"unknown xor_type 0x{xor_type:02x}")


def generate_key(params: XorParams) -> bytes:
    """The 256-byte key table."""
    return bytes((i * params.delta) & 0xFF for i in range(256))


def decrypt(data: bytes) -> tuple[bytes, XorParams]:
    """Decrypt a session body. Returns the plaintext and the parameters used."""
    if len(data) < PLAINTEXT_HEADER:
        raise DecryptError("file shorter than the plaintext header")

    params = xor_params(data)
    key = generate_key(params)

    out = bytearray(data)
    if params.xor_type == XOR_TYPE_PT59:
        for i in range(PLAINTEXT_HEADER, len(out)):
            out[i] ^= key[i & 0xFF]
    else:
        for i in range(PLAINTEXT_HEADER, len(out)):
            out[i] ^= key[(i >> 12) & 0xFF]

    return bytes(out), params


def decrypt_file(path: str) -> tuple[bytes, XorParams]:
    with open(path, "rb") as fh:
        return decrypt(fh.read())


def is_decrypted(data: bytes) -> bool:
    """True when the body reads as a plaintext block chain.

    A byte histogram cannot answer this: random data contains 0x5A at about
    1/256, which is well above any useful `Z`-count threshold, and XOR output
    has a similar distribution. The only reliable test is structural — walk the
    top-level chain and require it to start at 0x1F, tile without gaps, and
    reach the end of the file.
    """
    if len(data) <= PLAINTEXT_HEADER:
        return True

    from .ptblocks import HEADER_SIZE, _read_block

    big_endian = bool(data[0x11])
    pos = 0x1F
    blocks = 0
    end = len(data)
    while pos + HEADER_SIZE <= end:
        block = _read_block(data, pos, end, big_endian)
        if block is None:
            return False
        pos = block.end
        blocks += 1
        if blocks > 1_000_000:
            return False
    # a real session ends on a block boundary; allow a few trailing bytes
    return blocks >= 2 and end - pos <= 8
