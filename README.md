# conformkit

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
- **Binary AAF** is written with a real AAF library, and the container is
  readable without one.
- **Desktop UI** with a dense, keyboard-friendly list view and a fully modular
  command layer.
- **415 tests**, including validation against production edit lists and a
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
    # name and source are always right; timing carries a confidence rating
    print(f"{r.name[:40]:<40} src={r.source_index} "
          f"{r.length/48000:7.1f}s  {r.confidence}")
```

Session writing is deliberately narrow: only **same-length name replacement on
a copy**, verified by re-walking the block tree afterwards.

```python
from conformkit.ptwrite import write_clone

report = write_clone("in.ptx", "out.ptx", {"Old Name": "New Name"})
print(report.summary())   # "5 renamed, verified"
```

---

## Format support

| Format | Read | Write | Notes |
|---|---|---|---|
| CMX 3600 EDL | yes | yes | plus File16 / File32 / File129 |
| AAFX 2.0 (XML) | yes | yes | dependency-free |
| Binary AAF (OLE2) | container + names | yes (via pyaaf2) | objects keyed by SMPTE UL |
| FCP7 XML (`xmeml`) | partial | no | sequence / clipitem / file |
| Pro Tools session (PTX/PTF/PTS) | identification, decryption, block tree, names, regions, embedded XML | name replacement only | AES-free XOR with a derived 256-byte key |
| BWF / WAV | yes | no | `fmt `, `data`, `bext`, `iXML` |
| MIDI (SMF type 1) | no | yes | markers, cues, punches, streamers |
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
├── aaf.py          AAFX read/write, binary container identification
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
415 passed, 1 skipped
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
- **Structural verification** for decryption: a wrong key yields no block
  chain at all, so the test asserts that blocks tile the file without gaps.
- **Transactional updates**: a mid-way failure leaves the list untouched.
- **Safe session writes**: block count and file size unchanged, source
  byte-identical.

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
- Binary AAF export needs `pyaaf2`; AAFX is always available.
- FCP7 XML import is partial; export is not implemented.
- No DAW automation: re-conform produces a plan and a new list, and leaves
  applying it to the operator.
- Session region **timing** is decoded with a confidence rating: about 23% of
  records resolve unambiguously in a production Atmos session, 74% have more
  than one plausible interpretation, and 3% none. Those records still carry
  their name and source index. Nothing downstream relies on low-confidence
  timing.
- Region *names* and source indices are reliable for every record.

---

## Licence

MIT. See [LICENSE](LICENSE).

This project interoperates with file formats. It contains no copy-protection
circumvention and implements no vendor's licensing, activation or DRM.

Third-party credit: the session decryption scheme and block layout were
established by [libptformat](https://github.com/zamaudio/ptformat) (LGPL-2.1+).
This is an independent Python re-derivation, not a port of that code.
