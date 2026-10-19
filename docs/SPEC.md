# Behavioural Specification — Edit-List and Conform Toolkit

**Version** 2.0
**Scope** The behaviour a film/TV sound post-production edit-list tool must
exhibit: file formats, algorithms, and workflow. Derived from published
standards plus black-box observation of files and workflows in the field.

This document describes **what must be true**, not how any particular product
implements it. Where a rule could not be established from an authoritative
source, it is marked `[DERIVE]` and the implementation documents its own rule
explicitly.

---

## 0. How to use this document

| Reader | Use |
|---|---|
| Implementer | treat each numbered requirement as testable |
| Reviewer | check the conformance suite in §16 covers every `[DERIVE]` |
| Integrator | §2 and §4-§8 define the interchange contracts |

Evidence tags:

```
[STD]     published standard (SMPTE / AMWA / EBU / Microsoft / Avid docs)
[OBSERVED] behaviour confirmed by inspecting files or workflows
[DERIVE]  not established — implementer must define and document a rule
```

---

## 1. Scope

Six functional pillars:

```
P1  Edit-list I/O        EDL, AAF, FCP7 XML, session text, delimited text
P2  Compare              diff two picture cuts -> change list
P3  Re-conform           re-cut a DAW session to a new cut
P4  Assembly             build a session/AAF from location WAVs
P5  Match / relink       bind events to audio by roll or filename
P6  Script import        extract scene data from PDF/text scripts
```

Supporting: **Update Data** (versioned list update), **column text
transforms**, project persistence, preferences.

---

## 2. Domain model

```
Project
 ├── EditList[]
 │    └── Event[]
 ├── ChangeList
 ├── WavLibrary
 ├── MatchSettings
 ├── CompareSettings
 ├── ColumnTransforms
 └── Preferences
```

### 2.1 Event

```
recIn, recOut        : Timecode
srcIn, srcOut        : Timecode
rollName             : string
clipName             : string
trackSel             : TrackSet        (structured)
trackSelText         : string          (EDL column text)
transition           : {kind, durationFrames, wipeCode}
```

Optional: eventNo, comments, sourceFile, scene, take, vfxUpdate, badSpeed,
srceFrameCount, destFrameCount, offset, speed, fileName, trackName, type,
info, annotations.

### 2.2 Track selection

Two representations, lossless conversion required:

```
parseTrackText(text) -> TrackSet
formatTrackText(set) -> string
```

Accepted alphabet `[OBSERVED]`: `V`, `A`, `A2`, `A3`, `A4`, `AA`, `B`,
and combinations joined with `/` (`A2/V`).

### 2.3 Display columns

Required toggleable columns (fixed ones in **bold**):

**eventNo** | **rollName** | **sourceIn** | **sourceOut** | **destIn** |
**destOut** | track | trackSel | transition | clipName | badSpeed |
vfxUpdate | comments | srceDurn | destDurn | srceFrameCount | offset |
speed | type | fileName | info

---

## 3. Timecode engine `[STD]`

### 3.1 Rates

```
23.976 / 24          25
29.97 / 30 (DF+NDF)  47.952 / 48
59.94 / 60 (DF+NDF)  90 (AAF input only; converted to 30)
```

### 3.2 API

```
parse(text) -> Timecode | error
format(tc, rate, df) -> text
toFrames(tc) -> int64
fromFrames(n, rate, df) -> Timecode
add, sub, compare
```

### 3.3 Drop-frame

29.97 DF: drop 2 frame numbers per minute except every 10th.
59.94 DF: drop 4, except every 10th.
Real-time conversion uses the exact 1000/1001 ratio, never the nominal rate.

### 3.4 Acceptance vectors `[STD]`

| Input | Rate | Frames |
|---|---|---|
| `00:00:00:00` | any | 0 |
| `00:01:00;02` | 29.97 DF | 1800 |
| `00:00:59;29` | 29.97 DF | 1799 |
| `00:10:00;00` | 29.97 DF | 17982 |
| `01:00:00:00` | 25 NDF | 90000 |
| `00:01:00;04` | 59.94 DF | 3600 |

### 3.5 Frame-count variants

Distinguish: list rate, session rate, session NPF count, destination count,
source count. Conversions between them are explicit operations.

### 3.6 Errors

Invalid timecode names the offending token: `Error reading TC format. [token]`.

---

## 4. EDL `[STD]` `[OBSERVED]`

### 4.1 Dialects

| Dialect | Roll-name limit |
|---|---|
| CMX 3600 | 32 (writer); standard CMX is 8 |
| File16 | 16 |
| File32 | 32 |
| File129 | 129 |
| No Format | n/a |

Writer dialect is sticky until changed.

### 4.2 Grammar

```
document := header* event*
header   := "TITLE:" text | "FCM:" ("DROP FRAME"|"NON-DROP FRAME") | comment
event    := evnum SP reel SP track SP transition SP
            [duration SP] srcIn SP srcOut SP recIn SP recOut (comment)*
transition := "C" | "D" | "W"[pattern] | "K"
comment  := "*" SP text
```

Separator: `:` NDF, `;` DF.

### 4.3 Real-world tolerance

Must accept, without rejecting:

```
- zero-length events (transition anchors)
- a dissolve written as two lines sharing an event number
- wipes with two numeric parameters (pattern + duration)
- annotation lines: "AUD n n", "M2 <roll> <speed> <tc>", ">>> ..."
- "EFFECTS NAME IS ..."
- "REM ..." free text
- comments without the space after '*'
- source/record duration mismatch (speed changes, freeze frames)
- roll names up to the dialect limit
```

Must reject, with a distinct message each:

```
- truncated event line
- unparseable timecode
- roll name longer than the dialect allows
- non-monotonic event numbers
- FCM contradicting the frame rate
- a cut carrying a duration
- a transition without a duration
- negative duration
```

### 4.4 Export comment lines

```
* FROM CLIP NAME:
* SOURCE FILE:
* SOURCE CUT LIST FILE SELECTED
* FRAME COUNT OF SOURCE:
* FRAME COUNT OF DESTINATION:
* CREATED USING:
* TEXT ENCODING:
* DATE AND TIME:
```

### 4.5 Roll-name policy

```
allowed chars: ~!@#$%^&*()-_=+[{]}\|;:/?,.<>
options: force uppercase; restrict to ASCII; truncate to N
```

Never silently alter a roll name: always report what changes.

### 4.6 Import pipeline

```
1 initialise source list
2 run validation checks
3 load events
4 merge multiple source EDLs
5 sort and flatten
```

### 4.7 Import settings

```
extend selected events to fill gaps
offset selected source timecodes
extend events to fill gaps
offset source timecodes
only load comments containing <text>
only load tracks with specific tags
only load active tracks
ignore aux events
TC format override
```

### 4.8 Multi-EDL merge

Reject overlapping record ranges (do not silently sort), renumber, define a
roll-collision policy, reject frame-rate mismatch, expose list ordering.

---

## 5. Column text transforms

A per-column ordered pipeline.

### 5.1 Operation kinds

```
replace text      find, replaceWith, occurrence
insert text       text, charPos
remove characters chars
remove by occurrence  find, occurrence, removeOccurrence
remove around anchor  anchor, occurrence, mode(before|after), removeText
truncate          maxChars
substitute date   charPos, source, target, occurrence
conditional       guard, inner transform
source routing    dataFrom, dataTo, commContains
```

### 5.2 Requirements

Deterministic; previewable; undoable.

---

## 6. AAF `[STD]`

### 6.1 Standard

AMWA AAF 2.0. Binary AAF is an OLE2 compound file whose objects are keyed by
SMPTE Universal Labels. AAFX (XML) is the dependency-free interchange form.

### 6.2 Mapping

| Domain | AAF |
|---|---|
| EditList | CompositionMob, one timeline |
| Event | SourceClip (Filler for gaps) |
| recIn/recOut | clip start / length |
| srcIn/srcOut | source mob slot + start |
| Track | TimelineMobSlot |
| Roll name | Mob name |
| Clip name | Source mob name |
| Marker | EventMobSlot |
| Transition | OperationGroup |

### 6.3 Element tags recognised on import

```
EG EssenceGroup   FL Filler        NS NestedScope   OG OperationGroup
PD Pulldown       SE Selector      SQ Sequence      SC SourceClip
TR Transition     SR ScopeReference
```

### 6.4 Data definitions

```
Picture  Sound  LegacySound  Timecode  LegacyTimecode  Edgecode
DescriptiveMetadata
```

Unknown type: reject with a named error.

### 6.5 Track placement `[DERIVE]`

Required behaviour:

```
- a clip may not overlap another clip on the same track
- track names derived from the track assignment
- empty tracks removed
- optional end-trim when a clip would overrun the next clip
- output track count minimal for the given assignment
```

The exact rule is implementation-defined; document it and test it.

### 6.6 Import settings

```
import source timecodes using sequence frame count
import video tracks as source
load 30/24 XMLs at 30/24
load mute clips
sources into comments
filename source tag
roll-name source tag
```

### 6.7 Export settings

```
TC rate  audio pull rate  sample rate  bit depth
channel data mode  channel names
track name config: conform / picture / scene
start round to minutes
```

### 6.8 Warnings

Never silently change rate. Advise when:

```
- the TC rate cannot be represented by the target DAW version
- the audio pull rate is unsupported
- session start must match AAF start
- older DAW versions have rate limitations
```

---

## 7. FCP7 XML (`xmeml`)

| Domain | xmeml |
|---|---|
| EditList | `<sequence>` |
| Event | `<clipitem>` |
| recIn/recOut | `<start>` / `<end>` |
| srcIn/srcOut | `<in>` / `<out>` |
| Media | `<file><pathurl>` |
| Track | `<track>` |

---

## 8. DAW session interchange

### 8.1 Binary session format `[DERIVE]`

Proprietary and undocumented. Requirements:

```
- identify by public registry signature
- decrypt the body before parsing
- walk the block tree with bounded recursion
- recover names, sources and regions
- NEVER write a session from scratch
```

Writing is limited to **same-length name replacement on a copy**, verified by
re-walking the block tree afterwards.

### 8.2 Re-conform mechanism

Two acceptable mechanisms:

1. **Session/AAF generation** — preferred: produce a new session or AAF.
2. **UI automation** — keystroke injection into the DAW. If chosen, the tool
   must check and refuse when the DAW is not in the required state:

```
- no background tasks in the DAW
- only the edit window open
- target tracks visible and unlocked
- target tracks selected
- correct video frame rate
- session start <= earliest new event
- run elevated if the DAW is elevated
```

### 8.3 Re-conform state to persist

```
last conform duration / markers / redo event numbers / copy-cut mode
conform attempt number, result, keystroke delay
```

---

## 9. WAV / BWF `[STD]`

Chunks: `fmt `, `data`, `bext`, `iXML`, `axml`, `chna`.

Derivations:

1. start timecode from `bext` TimeReference (sample offset / sample rate)
2. roll name from iXML `TAPE`, else `bext` originator — document precedence
3. scene / take from iXML
4. channel count validated against actual channel data
5. duplicate detection
6. poly WAV must support split-to-mono and single multi-channel clip

---

## 10. Compare

### 10.1 Settings axes

```
match on: roll | clip | both
tolerance in frames
moved event treated as change vs new
report deletions
report gaps
```

### 10.2 Change record

```
changeType : added | removed | moved | trimmed | unchanged
old, new, delta, detail
```

### 10.3 Requirements

Off the UI thread with progress; deterministic; flag a list dominated by ±1
frame changes; reject multi-track change lists where a single track is
required; O(n log n) on thousands of events.

---

## 11. Update Data

Apply a versioned change list to a list at the version it was produced for.

```
update timecodes in: destination | source | both
update version field: name + value
delete tagged entries: tag
fallback for un-updatable timecodes: replace with / append after
create a column: header name
```

**Transactional**: any failure leaves the list untouched. Match by event
number when present, otherwise by roll — never both, or one entry will apply
to every event sharing a roll.

---

## 12. Match / relink

Modes: roll name, file name, both.

```
agreement flags: diffMatchAndClip, diffRollNames, diffClipNames
```

Rules:

1. roll takes precedence when both are available and disagree
2. unmatched events are retained and flagged, never dropped
3. case-insensitive by default
4. ALE input supplies sound-roll mapping

---

## 13. Assembly

1. scan folder recursively for WAV
2. read bext/iXML, derive start timecode
3. group by scene / take / roll
4. place clips; resolve overlaps by a documented deterministic rule
5. export AAF or session at the chosen rate
6. generate mute WAVs where a channel is absent
7. optional end-trim
8. warn on poly WAVs before AAF export

Default timeline rate: the most common rate across loaded WAVs.

---

## 14. Script import

- PDF text extraction including password-protected files
- layout-aware: scene number may be left or right of the page
- output `(sceneNumber, description, pageNumber)`
- debug view before commit
- plain-text fallback
- output: markers on a scene-change track with a configurable prefix

---

## 15. Persistence

### 15.1 Native formats

```
project, edit list, WAV library, assembly settings
```

Required behaviour, not byte layout:

| Requirement | Detail |
|---|---|
| self-identifying header | reject with "no valid header" |
| version stamp | warn on mismatch |
| single project | opening a second is refused |
| atomic save | locked target fails cleanly, never truncates |
| forward compat | newer file in older app warns, does not crash |

### 15.2 Preferences

```
location : per-user config directory
encoding : UTF-8
format   : key/value text
```

Must persist: window geometry, column widths and visibility, last-used paths,
import/export settings, match and compare settings, recent files.

---

## 16. Conformance suite

### 16.1 Timecode

```
for rate in {23.976,24,25,29.97,47.952,48,59.94,60}:
  for df in {false,true}:
    for tc in boundary_set:
      assert fromFrames(toFrames(tc)) == tc
plus the six vectors in §3.4
```

### 16.2 EDL round trip

```
for dialect in {CMX3600, File16, File32, File129}:
  1000 events, all track combos, all transitions, max roll names, DF and NDF
  export -> import -> deep equality
  write twice -> byte-identical
```

### 16.3 EDL negatives

Each case in §4.3 must produce its own error and no partial list.

### 16.4 AAF

```
events -> AAF -> reimport -> set equality
no clip overlaps on any track
track count minimal
unknown DataDef rejected with a named error
binary output is a valid OLE2 compound file
```

### 16.5 Compare

```
run twice -> identical output
shuffle input -> same change list after canonical sort
single-track requirement enforced
```

### 16.6 BWF

Synthetic WAVs with known `bext` TimeReference and iXML `TAPE` yield the exact
expected start timecode and roll name. Mono and poly.

### 16.7 Column transforms

Table-driven per operation kind, including the conditional guard.

### 16.8 Update Data

Versioned change list applied to a known list yields exact expected output.
Incompatible version rejected. Failure leaves the list unmodified.

### 16.9 Conform

Synthetic old cut + new cut with ground truth; applied operations reproduce
the new cut exactly.

### 16.10 Session write

A same-length rename must preserve the block count and file size, and the
source must remain byte-identical.

---

## 17. Non-goals

- Writing a binary session format from scratch.
- Byte-compatibility with any vendor's private project formats.
- Copy protection, licensing, or activation of any kind.
- Vendor UI artwork, icons or branding.
