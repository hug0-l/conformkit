# conformkit

[![tests](https://github.com/hug0-l/conformkit/actions/workflows/test.yml/badge.svg)](https://github.com/hug0-l/conformkit/actions/workflows/test.yml)

**Status: alpha.** The format layer is well tested; the desktop UI is usable but
still gaining features. Expect API changes before 1.0.

An edit-list and conform toolkit for film/TV sound post-production: read and
write EDLs, inspect and export AAF, plan a re-conform against a new picture
cut, and assemble a timeline from location recordings.

Built from public format standards (SMPTE 12M, CMX 3600, AMWA AAF 2.0, EBU
Tech 3285, [MS-CFB], SMF) and validated against production material.

---

## Highlights

- **No runtime dependencies** for the core. PDF import and binary AAF export
  are opt-in extras.
- **Edit lists round-trip byte-stable** across four dialects, including the
  parts of real-world EDLs that textbooks omit: dissolve pairs, zero-length
  anchors, CDL annotations, `REM` blocks.
- **Any file can be identified** from its content, not its extension.
- **AAF-XML v1.1** is written in the AAF SDK's XML stored format and validated
  against its reference schema; **binary AAF** is written with a real AAF
  library, and the container is readable without one.
- **Desktop UI** with a dense, keyboard-friendly list view and a fully modular
  command layer.
- **458 tests**, including validation against production edit lists and a
  third-party parser.

---

## Install

```bash
git clone https://github.com/hug0-l/conformkit
cd conformkit
pip install -e .

# optional extras
pip install -e ".[pdf]"    # PDF script import
pip install -e ".[aaf]"    # binary AAF export
pip install -e ".[cross]"  # cross-validation against other parsers
pip install -e ".[dev]"    # pytest
```

Python 3.10+. The core installs nothing.

---

## Development

```bash
python -m pip install -e ".[aaf,pdf,dev]"
python -m pytest tests -q
```

`fixtures/` and `work/` are gitignored, so tests that need production
material skip on a clean checkout. The public fixture corpus can be fetched
with `bash scripts/fetch_fixtures.sh`.

---

## Command line

```bash
conformkit identify mystery_file        # content-based identification
conformkit info show_r1.edl             # summarise an edit list
conformkit convert in.edl out.aafx      # EDL <-> AAFX
conformkit compare old.edl new.edl      # diff two cuts
conformkit conform session.edl new.edl  # plan a re-conform
conformkit assemble ./wavs --out a.edl  # build from location WAVs
conformkit wavinfo take_01.wav          # dump BWF metadata
conformkit ptx session.ptx --tracks 20  # inspect a DAW session
conformkit aafinfo cut.aaf --names 20   # inspect an AAF container
conformkit gui show_r1.edl              # desktop window
```

All commands take `--rate` and `--encoding`, and name the offending item on
failure (`line 42: event 17 source duration 25 != record duration 24`).

---

## Desktop UI

```
conformkit gui            # or: conformkit-gui
```

```
+-------------------------------------------------------------------+
| File  Edit  List  View  Help                                      |
+-------------------------------------------------------------------+
| Open List | Save List | Import▾ | Append▾ | Clear Events |        |
| Display Options | Clean Events | Compare Lists |                  |
| WAV File List | Assemble WAVs | Update Data | PT Re-conform |     |
| Export▾ | User Guide                       Frame Count [25 v]      |
+-------------------------------------------------------------------+
| Sequence 01  (3 warnings)                           test_25.edl   |
| Track [All v]  Roll [    ]  Search [        ]  Clear Filters      |
+-------------------------------------------------------------------+
| EvNo | + | RollName | Source In | Source Out | Destination In |...|
+-------------------------------------------------------------------+
| Events 13   Rolls 2   Frame Count 25                              |
+-------------------------------------------------------------------+
```

The command set is declarative — the toolbar and menus reference command ids,
so adding an action is one method plus one registry line. See
[`docs/GUI.md`](docs/GUI.md).

---

## Library

```python
from conformkit.edl import parse_edl, write_edl
from conformkit.timecode import rate_for

rate = rate_for("25")
edit_list = parse_edl(open("cut.edl").read(), rate)

print(len(edit_list), "events")
print(edit_list.events[0].rec_in, edit_list.events[0].clip_name)

open("out.edl", "w").write(write_edl(edit_list, "CMX3600"))
```

### Compare and re-conform

```python
from conformkit.compare import compare_lists
from conformkit.conform import check_reconformable, plan_conform

changes = compare_lists(old, new)
print(changes.counts())          # {'moved': 12, 'trimmed': 3}

problems = check_reconformable(session, new_cut)
if not problems:
    plan = plan_conform(session, new_cut)
    for op in plan.ops:
        print(op.kind.value, op.delta, op.detail)
```

### MIDI export

```python
from conformkit.midi import MidiSettings, write_midi

settings = MidiSettings(
    text_cue_track=True,
    text_cue_advance_seconds=2.0,
    add_pull_down=True,
)
write_midi("cues.mid", edit_list, rate, settings)
```

Type-1 SMF with the conductor track first and 960 ticks per quarter note.
Markers, notes, punches, streamers and text cues each get their own track.

- **Text cues** are standard SMF text events (`FF 01`) at each event's
  record-in position, not markers. They carry the clip name, prefixed with the
  three-digit event number when `marker_name_index` is set.
- **`text_cue_advance_seconds`** moves text cues earlier, clamped at tick 0.
  Only the text-cue track is affected; negative values are rejected.
- **`add_pull_down`** interprets exported times at 1000/1001, so 24/30 fps
  material lands on the 23.976/29.97 grid. Every generated track uses that
  scale consistently. It is an export-time interpretation: the edit list is
  not modified.

### Binary AAF

```python
from conformkit.aafwrite import write_binary_aaf

summary = write_binary_aaf("out.aaf", edit_list, rate, title="Episode 4")
print(summary)   # {'events': 128, 'tracks': 6, 'size': 1421312, ...}
```

### DAW sessions

```python
from conformkit.ptx import identify, read_session

fmt = identify(open("session.ptx", "rb").read(), "session.ptx")
print(fmt.kind, fmt.pronom)               # PTX fmt/1727

s = read_session("session.ptx")
print(s.session_name, s.sample_rate, s.block_count)
print(s.track_names[:5], s.plugins[:5])

for r in s.regions[:5]:
    # sample_offset and length are validated against the real source file
    print(f"{r.name[:40]:<40} src={r.source_index} "
          f"{r.duration_seconds:7.1f}s at offset {r.sample_offset:,}")

for f in s.audio_files[:5]:
    print(f"{f.name:<44} {f.length:>10,} samples "
          f"@ {f.sample_rate} Hz / {f.channels} ch")
```

Session writing is deliberately narrow: only **same-length name replacement on
a copy**, verified by re-walking the block tree afterwards.

```python
from conformkit.ptwrite import write_clone

report = write_clone("in.ptx", "out.ptx", {"Old Name": "New Name"})
print(report.summary())   # "5 renamed, verified"
```

### Session format, as verified

The layout below is established from the files and checked against real audio,
not taken on trust. Two independent sessions are used: a public demo that ships
its own WAVs, and a 34 MB production Atmos session.

**Decryption.** The first `0x14` bytes are plaintext. Byte `0x12` selects the
key schedule, byte `0x13` seeds it:

```
delta = the i in 0..255 with (i * mul) & 0xFF == xor_value
        mul = 53, positive, for type 0x01
        mul = 11, negative, for type 0x05
key[i]   = (i * delta) & 0xFF
key index = off & 0xFF          for type 0x01
          = (off >> 12) & 0xFF  for type 0x05
```

**Blocks.** `Z` (0x5A) at `pos`, u16 type at `+1`, u32 size at `+3`, u16 content
type at `+7`. `size` counts from the content-type field, so a block ends at
`pos + 7 + size`. Children sit inside the parent payload.

**Audio files.** Names come from content type `0x103A`; the record with the
length is `0x1001` nested under `0x1003`, in the same order.

```
0x1001 : +0 ct, +2 u32 sample rate, +6 u8 channels, +7 u8 bits,
         +8 u64 length in samples
```

**Regions.** Content type `0x2629` (or `0x1008`) inside `0x262A` (or `0x100B`).
Name length is a u32 at `payload+11`, name bytes at `payload+15`. A packed
triple follows: field widths are the high nibbles of the three bytes at
`base+1..+3`, and the values are **little-endian** starting at `base+5`, in the
order `sample_offset, length, start`.

The endianness is not a style choice — it is provable. Reading the triple
big-endian leaves only 22% of regions inside their source file; little-endian
gives 100%. That assertion is in the test suite.

---

## Format support

| Format | Read | Write | Notes |
|---|---|---|---|
| CMX 3600 EDL | yes | yes | plus File16 / File32 / File129 |
| AAF-XML v1.1 | yes | yes | AAF SDK XML stored format, schema-validated; not a DAW interchange format |
| Binary AAF (OLE2) | container + names | yes (via pyaaf2) | objects keyed by SMPTE UL |
| FCP7 XML (`xmeml`) | partial | no | sequence / clipitem / file |
| Pro Tools session (PTX/PTF/PTS) | identification, decryption, block tree, tracks, regions, audio files, embedded XML | name replacement only | AES-free XOR with a derived 256-byte key |
| BWF / WAV | yes | no | `fmt `, `data`, `bext`, `iXML` |
| MIDI (SMF type 1) | no | yes | markers, cues, punches, streamers, text cues |
| PDF / text script | yes | n/a | PDF needs `pypdf` |
| Delimited text | yes | yes | tab and CSV |

---

## Architecture

```
conformkit/
├── timecode.py     SMPTE timecode, drop-frame, all rates
├── model.py        Event, EditList, TrackSet, Transition, Marker
├── edl.py          EDL parse / serialise, four dialects
├── bwf.py          WAV / BWF / iXML metadata
├── transforms.py   column text transform pipeline
├── compare.py      change list
├── match.py        roll / filename matching
├── aaf.py          AAF-XML v1.1 read/write, binary container identification
├── aafwrite.py     binary AAF via pyaaf2 (optional)
├── cfb.py          MS-CFB (OLE2) reader
├── conform.py      re-conform planning
├── assembly.py     timeline from location WAVs
├── script.py       shooting-script import
├── midi.py         SMF type 1 writer
├── update.py       versioned change list
├── ptx.py          session identification and metadata
├── ptdecrypt.py    session body decryption
├── ptblocks.py     session block tree
├── ptregions.py    session audio files and regions
├── ptwrite.py      safe same-length session edits
├── guimodel.py     Tk-free view model
├── gui/            desktop UI (app, panels, commands, widgets, dialogs)
└── cli.py          command line
```

Each module is a pure function library over plain dataclasses. Nothing touches
the network, the registry, or the filesystem except the explicit loaders.

---

## Extension points

| Add | Where |
|---|---|
| a document format in the UI | `conformkit/gui/panels.py` — `register_panel()` |
| a user action | `conformkit/gui/operations.py` — one method, one registry row |
| a toolbar button or menu item | `conformkit/gui/commands.py` — `TOOLBAR` / `MENUS` |
| a dialog | `conformkit/gui/dialogs.py` |
| a CLI subcommand | `conformkit/cli.py` — `build_parser()` |

---

## Testing

```bash
python -m pytest tests -q
```

```
458 passed, 1 skipped
```

What the suite actually proves:

- **Standard timecode vectors** that can be checked independently of any
  product.
- **Real-world EDL validation** against production lists, including dissolve
  pairs, CDL/FRMC annotations, and generator output.
- **Cross-validation against an independent parser**: edit counts, source
  identifiers, timecodes, clip names and transitions must agree edit-for-edit.
- **Round-trip stability**: parse → write → parse is byte-identical.
- **Negative tests**: every malformed input produces a distinct error and no
  partial list.
- **Container integrity**: AAF output is a well-formed compound file, read back
  by our own independent CFB implementation.
- **Ground-truth validation against real audio**: a public demo session ships
  its own WAVs, so every region's `sample_offset + length` is checked against
  the actual file length, and every file length in the session is checked
  against the WAV header. Both must match exactly.
- **Endianness pinned by evidence**: the same test asserts that reading the
  packed triple big-endian *breaks* the bound wholesale, so the little-endian
  choice is proven rather than assumed.
- **Structural verification** for decryption: a wrong key yields no block
  chain at all, so the test asserts that blocks tile the file without gaps.
- **Transactional updates**: a mid-way failure leaves the list untouched.
- **Safe session writes**: block count and file size unchanged, source
  byte-identical.

AAF-XML output can be checked against the AAF SDK's reference schema. The
validator downloads the schema at run time (it is not vendored) and needs
`lxml`; the test suite itself needs neither:

```bash
python scripts/validate_aafxml.py out.aafx
```

Fixtures from third parties are not vendored:

```bash
bash scripts/fetch_fixtures.sh
```

Private production fixtures live in `fixtures/production/` and are gitignored;
tests using them skip automatically.

---

## Performance

Measured on a 34 MB Dolby Atmos session (48 kHz, 118,950 blocks):

| Stage | Time |
|---|---|
| decrypt 34 MB | 2.3 s |
| parse block tree | 2.9 s |
| extract string table | 1.6 s |
| extract 3,491 XML documents | 1.2 s |
| full `read_session` | ~7 s |

Everything is off the UI thread with progress reporting.

---

## Limitations

- Binary session writing is **name replacement only**; sessions are never
  synthesized.
- Binary AAF export needs `pyaaf2`; AAF-XML is always available. The AAF-XML
  writer implements the AAF SDK's XML stored format
  (`http://www.aafassociation.org/aafx/v1.1/20050628`, root `<AAF
  version="1.1">`) and is checked against the SDK reference schema by
  `scripts/validate_aafxml.py`. **It is not claimed to be importable by Pro
  Tools, Nuendo or Resolve.** AAF-XML is the SDK's XML representation, not the
  interchange file those applications consume; binary AAF remains the
  interoperable route.
- Transitions are not serialised into AAF-XML. AAF `Transition` objects require
  operation definitions that are file extensions rather than baseline
  dictionary entries, so the writer does not fabricate them. Clips keep their
  record and source timing, and `read_aafx` returns cuts.
- FCP7 XML import is partial; export is not implemented.
- No DAW automation: re-conform produces a plan and a new list, and leaves
  applying it to the operator.
- Session region **timing** is decoded and validated: every region's
  `sample_offset + length` is checked against the real source-file length, and
  holds for 100% of records in both test sessions. `start` (the third packed
  value) is reported but its meaning is not established, so nothing uses it.
- Session writing is **name replacement only**; sessions are never synthesized.

---

## Licence

MIT. See [LICENSE](LICENSE).

This project interoperates with file formats. It contains no copy-protection
circumvention and implements no vendor's licensing, activation or DRM.

Third-party credit: the session decryption scheme and block layout were
established by [libptformat](https://github.com/zamaudio/ptformat) (LGPL-2.1+).
This is an independent Python re-derivation, not a port of that code.
