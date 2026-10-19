"""Pro Tools session files: PTX (v10+), PTF (v7-9), PTS (v5.1-6.9).

Format identification follows the PRONOM signatures
(fmt/1727 for PTX, fmt/1951 for PTF/PTS), which are also cited by the Library
of Congress format description an archival format description.

Structure, established empirically against real production sessions (see
tests/test_ptx.py):

    offset 0   : 03                        block marker
    offset 1   : 16 ASCII bytes            version bit-string
    offset 17  : version / flags
    offset 20  : 5A 01 00 04 (PTX) or 01 01 (PTF/PTS)

    after that the body is a tree of typed blocks. Strings inside blocks are
    stored as a little-endian uint32 byte length followed by raw bytes, with no
    NUL terminator. Blocks also embed whole XML documents (channel
    configurations, plug-in state).

Avid publishes no format documentation; everything here is derived from the
files themselves plus the two public signatures.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

__all__ = [
    "PTXError", "SessionFormat", "identify", "PtxSession",
    "read_session", "extract_strings", "extract_xml_documents",
    "MAGIC_BITSTRING", "SIGNATURE_PTX", "SIGNATURE_PTF_PTS",
]

# --------------------------------------------------------------- signature

MAGIC_BITSTRING = b"0010111100101011"          # 16 ASCII bytes at offset 1
SIGNATURE_PTX = bytes.fromhex("5A010004")      # PTX, Pro Tools 10+
SIGNATURE_PTF_PTS = bytes.fromhex("0101")      # PTF/PTS, Pro Tools 5.1-9

PRONOM = {
    "PTX": "fmt/1727",
    "PTF": "fmt/1951",
    "PTS": "fmt/1951",
}


class PTXError(ValueError):
    pass


@dataclass(frozen=True)
class SessionFormat:
    kind: str                 # "PTX", "PTF", "PTS" or "unknown"
    pronom: str = ""          # PRONOM PUID
    version_bytes: bytes = b""
    confidence: str = "certain"

    @property
    def is_pro_tools(self) -> bool:
        return self.kind in ("PTX", "PTF", "PTS")


# --------------------------------------------------------------- identify

def identify(data: bytes, filename: str = "") -> SessionFormat:
    """Identify a Pro Tools session file.

    Uses the PRONOM byte sequences. Falls back to the extension when the
    signature is inconclusive, and says so via `confidence`.
    """
    # the shared prefix needs 19 bytes; the PTX tail needs 24
    if len(data) < 19:
        return SessionFormat("unknown", "", b"", "too short")

    if data[0:1] != b"\x03":
        return SessionFormat("unknown", "", b"", "no block marker")

    if data[1:17] != MAGIC_BITSTRING:
        return SessionFormat("unknown", "", b"", "version bit-string mismatch")

    # PTF/PTS carry 01 01 at offset 17 (PRONOM fmt/1951). There is no separate
    # version field: those two bytes sit exactly where PTX puts its version.
    if data[17:19] == SIGNATURE_PTF_PTS:
        version_bytes = data[17:19]
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext == "pts":
            return SessionFormat("PTS", PRONOM["PTS"], version_bytes)
        if ext == "ptf":
            return SessionFormat("PTF", PRONOM["PTF"], version_bytes)
        return SessionFormat("PTF", PRONOM["PTF"], version_bytes,
                             "signature shared by PTF/PTS; extension absent")

    version_bytes = data[17:20]

    # PTX carries 5A 01 00 04 at offset 20 (PRONOM fmt/1727)
    if len(data) >= 24 and data[20:24] == SIGNATURE_PTX:
        return SessionFormat("PTX", PRONOM["PTX"], version_bytes)

    return SessionFormat("unknown", "", version_bytes, "unrecognised tail")


# --------------------------------------------------------------- strings

_PRINTABLE = set(range(32, 127)) | {9, 10, 13}


def _looks_like_text(body: bytes) -> bool:
    if not body or b"\x00" in body:
        return False
    if any(c not in _PRINTABLE for c in body):
        return False
    return any(65 <= c <= 90 or 97 <= c <= 122 for c in body)


def extract_strings(data: bytes, min_len: int = 1, max_len: int = 4096):
    """Yield (offset, text) for every little-endian length-prefixed string.

    This is a scan, not a walk: the block tree is not fully decoded, so the
    only reliable way to enumerate the string table is to test each offset.
    """
    out = []
    n = len(data)
    off = 0
    limit = n - 5
    while off <= limit:
        ln = struct.unpack_from("<I", data, off)[0]
        if min_len <= ln <= max_len and off + 4 + ln <= n:
            body = data[off + 4:off + 4 + ln]
            if _looks_like_text(body):
                out.append((off, body.decode("ascii")))
                off += 4 + ln
                continue
        off += 1
    return out


# --------------------------------------------------------------- XML

XOR_KEY = 0xA1

# Pro Tools stores plug-in and renderer state as XML, and obfuscates parts of
# it with a single-byte XOR. The obfuscation is applied per region rather than
# per file, so both the plain and the XOR form must be considered.
_XML_ROOTS = ("dict", "plist", "root", "session", "DolbyAtmos", "atmos",
              "channelConfig", "channel-config", "AudioSuite", "RTAS")


def _xor(data: bytes, key: int = XOR_KEY) -> bytes:
    return bytes(b ^ key for b in data)


def _candidate_starts(stream: bytes, encoded: bool = False):
    """Offsets of every `<root…>` opening for the known XML roots, in one pass.

    When `encoded` is set the search is for the XOR form of each literal opening
    token. Because XOR is byte-wise, an encoded stream can be located inside the
    raw bytes without materialising a second full-size buffer. Only literals are
    transformed; the regex structure itself is left alone.
    """
    tokens = []
    for root in _XML_ROOTS:
        for suffix in (b">", b" "):
            tok = b"<" + root.encode("ascii") + suffix
            if encoded:
                tok = _xor(tok)
            tokens.append(re.escape(tok))
    pat = re.compile(b"(?:" + b"|".join(tokens) + b")")
    return [m.start() for m in pat.finditer(stream)]


def _balanced_from(stream: bytes, start: int, encoded: bool = False,
                   max_span: int = 262144):
    """Given an opening `<tag…>` at `start`, return the end offset or None.

    The search for the matching close is bounded by `max_span`. Embedded
    documents are small (a few hundred bytes to a few KB); an unbounded search
    over a 34 MB session is both slow and prone to matching unrelated bytes far
    downstream.
    """
    head = _xor(stream[start:start + 64]) if encoded else stream[start:start + 64]
    m = re.match(rb"<([A-Za-z_][\w.:-]{1,30})[\s>]", head)
    if not m:
        return None
    plain_tag = m.group(1)

    def tok(*parts: bytes) -> bytes:
        """Assemble a token, transforming the whole thing when encoded.

        Every byte must be transformed together; mixing a plain '<' with an
        encoded tag name produces a token that can never match.
        """
        raw = b"".join(parts)
        return _xor(raw) if encoded else raw

    open_tok = tok(b"<", plain_tag, b">")
    open_sp = tok(b"<", plain_tag, b" ")
    close_tok = tok(b"</", plain_tag, b">")
    gt = tok(b">")

    stop = min(len(stream), start + max_span)

    depth = 1
    pos = stream.find(gt, start, stop) + 1
    if pos <= 0:
        return None
    while depth and 0 < pos < stop:
        nxt_open = min([p for p in (stream.find(open_tok, pos, stop),
                                    stream.find(open_sp, pos, stop)) if p >= 0] or [-1])
        nxt_close = stream.find(close_tok, pos, stop)
        if nxt_close < 0:
            return None
        if 0 <= nxt_open < nxt_close:
            depth += 1
            pos = stream.find(gt, nxt_open, stop) + 1
            if pos <= 0:
                return None
        else:
            depth -= 1
            pos = nxt_close + len(close_tok)
    return pos if depth == 0 else None


def extract_xml_documents(data: bytes, min_size: int = 128, verify: bool = True):
    """Recover XML documents embedded in a session.

    Pro Tools stores plug-in and renderer state as XML and applies a single-byte
    XOR (0xA1) to some regions. Because XOR is byte-wise, the encoded form can be
    located by searching for the XOR of the opening tag itself, so no second
    full-size buffer is built.

    With `verify` set, a candidate is kept only if a real XML parser accepts it,
    so the result is well-formed rather than merely plausible.

    Returns a list of (offset, document_bytes, root_tag, was_xor).
    """
    import xml.etree.ElementTree as ET

    found = []
    seen = set()

    for encoded in (False, True):
        for start in _candidate_starts(data, encoded=encoded):
            if start in seen:
                continue
            end = _balanced_from(data, start, encoded=encoded)
            if end is None or end - start < min_size:
                continue
            chunk = data[start:end]
            if encoded:
                chunk = _xor(chunk)
            if verify:
                try:
                    ET.fromstring(chunk)
                except ET.ParseError:
                    continue
            m = re.match(rb"<([A-Za-z_][\w.:-]{1,30})[\s>]", chunk)
            seen.add(start)
            found.append((start, chunk, m.group(1).decode("ascii", "replace"),
                          encoded))

    found.sort(key=lambda x: x[0])
    return found


# --------------------------------------------------------------- session

@dataclass
class PtxSession:
    fmt: SessionFormat
    path: str = ""
    size: int = 0
    strings: list = field(default_factory=list)
    xml_documents: list = field(default_factory=list)

    # derived
    session_name: str = ""
    session_file: str = ""
    user_name: str = ""
    computer_name: str = ""
    working_path: str = ""
    track_names: list = field(default_factory=list)
    clip_names: list = field(default_factory=list)

    # block tree
    blocks: list = field(default_factory=list)
    block_count: int = 0
    xor_type: int = 0
    xor_value: int = 0
    big_endian: bool = False
    sample_rate: int = 0
    plugins: list = field(default_factory=list)

    @property
    def string_count(self) -> int:
        return len(self.strings)

    def text_at(self, offset: int) -> str | None:
        for off, text in self.strings:
            if off == offset:
                return text
        return None


# names that are never track or clip names: UI chrome, plug-in parameters,
# XML keys and format vocabulary
_NOISE = re.compile(
    r"^(Info #\d+|Untitled|"
    r"[A-Z][a-z]+ ?[A-Z]?[a-z]* (Listen )?(In/Out|Type|Frequency|Q|Gain)|"
    r".*<\/?(string|key|real|int|value)>.*|"
    r"[\d\W_]+)$"
)

# plug-in parameter names look like "Sc Filter Type", "EQ High-Mid Listen In/Out"
_PARAM_WORDS = ("Listen", "Filter", "Frequency", "Threshold", "Ratio", "Attack",
                "Release", "Gain", "Mix", "Depth", "Rate", "Out", "Bypass",
                "Enable", "Mode", "Amount", "Width", "Delay", "Feedback",
                "Pan", "Level", "Send", "Output", "Input", "Sync", "Mute",
                "Solo", "Automation", "Snapshot", "Preset", "Makeup", "Knee")


def _looks_like_track_or_clip(text: str) -> bool:
    if len(text) < 2 or len(text) > 128:
        return False
    if _NOISE.match(text):
        return False
    if any(w in text for w in _PARAM_WORDS) and " " in text and len(text) < 40:
        return False
    if text.startswith("<") or "</" in text:
        return False
    return True


def read_session(path: str) -> PtxSession:
    """Read a Pro Tools session file, extracting what is recoverable.

    The body is decrypted first (see `conformkit.ptdecrypt`), then the block
    tree is walked (see `conformkit.ptblocks`). Names are read from the block
    payloads as length-prefixed strings.
    """
    import struct

    from .ptblocks import parse_blocks, walk
    from .ptdecrypt import DecryptError, decrypt

    with open(path, "rb") as fh:
        data = fh.read()

    fmt = identify(data, path)
    if not fmt.is_pro_tools:
        raise PTXError(
            f"not a Pro Tools session file ({fmt.confidence})")

    sess = PtxSession(fmt=fmt, path=path, size=len(data))

    # --- decrypt
    try:
        plain, params = decrypt(data)
        sess.xor_type = params.xor_type
        sess.xor_value = params.xor_value
    except DecryptError as exc:
        raise PTXError(f"cannot decrypt session body: {exc}") from exc

    sess.big_endian = bool(plain[0x11])

    # --- block tree
    try:
        sess.blocks = parse_blocks(plain, big_endian=sess.big_endian)
        sess.block_count = sum(1 for _ in walk(sess.blocks))
    except Exception as exc:                        # noqa: BLE001
        raise PTXError(f"cannot walk the block tree: {exc}") from exc

    # --- strings and XML from the decrypted body
    sess.strings = extract_strings(plain)
    sess.xml_documents = extract_xml_documents(plain)

    # --- names from block payloads
    sess.track_names, sess.clip_names, sess.plugins = _names_from_blocks(
        plain, sess.blocks, sess.big_endian)

    # --- session metadata from the leading strings
    values = [t for _, t in sess.strings]
    for t in values[:40]:
        if t.startswith("Info #"):
            continue
        if not sess.user_name and re.fullmatch(r"[A-Z][A-Za-z0-9_\-]{2,31}", t):
            sess.user_name = t
            continue
        if not sess.computer_name and re.fullmatch(r"[A-Za-z0-9_.\-]{3,64}", t) \
                and sess.user_name:
            sess.computer_name = t
            continue
        if sess.computer_name and not sess.working_path and re.search(r"[\\/]", t):
            sess.working_path = t
            continue
        if t.lower().endswith((".ptx", ".ptf", ".pts")):
            sess.session_file = t
            sess.session_name = t.rsplit(".", 1)[0]
    if not sess.session_name and path:
        base = path.replace("\\", "/").rsplit("/", 1)[-1]
        sess.session_name = base.rsplit(".", 1)[0]

    # --- sample rate: a known audio rate stored as u32 at payload + 4.
    # Several blocks carry it; take the most frequent value, which is the
    # session rate rather than a per-file or per-bus override.
    from collections import Counter

    KNOWN_RATES = {8000, 11025, 16000, 22050, 32000, 44100, 48000,
                   88200, 96000, 176400, 192000, 352800, 384000}
    fmt_u32 = ">I" if sess.big_endian else "<I"
    rate_votes: Counter = Counter()
    for b in sess.blocks:
        if b.block_size < 8:
            continue
        val = struct.unpack_from(fmt_u32, plain, b.payload_offset + 4)[0]
        if val in KNOWN_RATES:
            rate_votes[val] += 1
    if rate_votes:
        sess.sample_rate = rate_votes.most_common(1)[0][0]

    return sess


# content types that carry a leading name, established empirically
_NAME_CONTENT = {
    0x251A: "track",     # track name
    0x1021: "bus",       # bus / output name
    0x2602: "bus",       # bus configuration name
    0x2628: "clip",      # region / clip name
    0x210B: "marker",    # marker or memory location
    0x2423: "clip",      # alternate region list
    0x1017: "plugin",    # plug-in name
    0x1014: "bus",
    0x1052: "bus",
}

_NAME_OFFSETS = (0, 1, 2, 3, 4, 6, 8, 12)


def _pstring_at(data: bytes, off: int) -> str | None:
    """A little-endian length-prefixed ASCII string, or None."""
    import struct

    if off + 4 > len(data):
        return None
    n = struct.unpack_from("<I", data, off)[0]
    if not (1 <= n <= 512) or off + 4 + n > len(data):
        return None
    body = data[off + 4:off + 4 + n]
    if b"\x00" in body:
        return None
    if not all(32 <= c < 127 for c in body):
        return None
    if not any(chr(c).isalpha() for c in body):
        return None
    return body.decode("ascii")


def _names_from_blocks(data: bytes, blocks, big_endian: bool):
    """Recover track, clip and plug-in names from block payloads."""
    from .ptblocks import walk

    tracks: list[str] = []
    clips: list[str] = []
    plugins: list[str] = []
    seen_tracks: set[str] = set()
    seen_clips: set[str] = set()
    seen_plugins: set[str] = set()

    for _, b in walk(blocks):
        kind = _NAME_CONTENT.get(b.content_type)
        if kind is None:
            continue
        name = None
        for d in _NAME_OFFSETS:
            name = _pstring_at(data, b.payload_offset + d)
            if name:
                break
        if not name:
            continue

        if kind == "track":
            if name not in seen_tracks:
                seen_tracks.add(name)
                tracks.append(name)
        elif kind == "clip":
            if name not in seen_clips:
                seen_clips.add(name)
                clips.append(name)
        elif kind == "plugin":
            if name not in seen_plugins:
                seen_plugins.add(name)
                plugins.append(name)

    return tracks, clips, plugins
