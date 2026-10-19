# Changelog

All notable changes to conformkit are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

## [0.1.0a2] - 2026-10-19

### Added

- AAF-XML v1.1 writer and reader in the AAF SDK's XML stored format
  (`http://www.aafassociation.org/aafx/v1.1/20050628`, root `<AAF
  version="1.1">`): header dictionary, composition and source mobs, timeline
  slots, sequences, fillers, source clips and PCM/CDCI descriptors. Mob ids are
  deterministic, so the same edit list produces byte-identical output.
  `scripts/validate_aafxml.py` checks documents against the SDK reference
  schema; the writer is not claimed to be importable by Pro Tools, Nuendo or
  Resolve, and binary AAF remains the interchange route.
- AAF-XML preserves `A1`/`A2` as separate slots and opens an extra lane when
  clips overlap on one EDL label, instead of collapsing them into one
  placement.
- Private-session region timing: the packed `sample_offset`, `length` and
  `start` triple is decoded with little-endian values, and each record carries
  a confidence rating. Validated against real audio, 100% of regions satisfy
  `sample_offset + length <= source length`; big-endian decoding satisfies only
  about 22%, which pins the byte order. Records with no source length carry
  `reliable=None` rather than an unchecked `True`.
- Session audio-file metadata: sample rate, channel count, bit depth and
  length in samples, read from the `0x1001` records nested under `0x1003` and
  paired with names from `0x103A`.
- Session timeline placements from the region-to-track map
  (`0x1054 -> 0x1052 -> 0x1050 -> 0x104f`): region index (u32 at `+2`),
  40-bit position (`+7`) and a unit byte (`+14`; samples or ticks). The demo
  session yields 286 placements whose ids all resolve, every sample position
  lies inside the session extent, and split regions from one source keep their
  source order in 97% of pairs (86% keep the exact gap). Tick placements are
  reported raw because tick-to-sample conversion needs the tempo map.
- `PtxSession.bus_names` from the `0x1021` I/O channel and `0x2602` I/O route
  lists. `0x1014` (audio track names) now contributes to `track_names`;
  `0x251A` is the MIDI track list.
- `--version` on the command line, including
  `python -m conformkit --version`.
- A single `conformkit.__version__` string, kept in sync with
  `pyproject.toml`.
- GitHub Actions test workflow for Python 3.10 to 3.13, including a summary
  of skipped tests so missing private fixtures stay visible.
- Packaging tests for version consistency and both CLI entry points.

### Changed

- AAFX output replaces the previous private simplified schema with real
  AAF-XML; the old private schema is no longer read.
- `extract_playlists()` now walks the real region-to-track map (`0x1054`)
  instead of scanning `0x1004`, which is the audio-file list and produced junk.
- Legacy `.pts` sessions now expose their track names from `0x1014` (for
  example, `RockBand.pts` has 15 named tracks instead of none).
- AAF export creates one `SourceMob` per source **and media kind**, with its
  single slot sized to the furthest extent any clip reads. `SourceClip`
  objects now reference the matching mob id and slot id instead of a
  placeholder.
- The dead `trim_ends` parameter was removed from both AAF writers.
- MIDI note events can take their duration from the edit event length rather
  than a fixed frame count.
- MIDI `add_pull_down` applies the 1000/1001 export-time scale to every
  generated track; `text_cue_advance_seconds` is rounded rather than truncated
  and rejects negative values.
- BWF chunk walking tolerates a missing pad byte after an odd-length chunk and
  pads a short `bext` chunk instead of discarding the whole file.
- The MIDI and AAF write tests assert observable behaviour rather than the
  exact bytes or framing of the output.

### Fixed

- `is_decrypted` now decides structurally: it requires the top-level block
  chain to start at `0x1F`, tile without gaps and reach the end of the file,
  instead of relying on a byte histogram.
- CFB reading is hardened against malformed containers: sector indices are
  range-checked, FAT and mini-FAT chains are cycle-checked, short chains and
  unsupported sector shifts raise `CfbError` rather than allocating without
  bound or silently truncating a stream.
- Big-endian session name length prefixes are decoded correctly, including in
  the committed `.pts` fixtures.
- Timecode parsing rejects negative fields, hours above 99, and drop-frame
  requests at rates that have no drop-frame variant.
- MIDI writing validates note and punch numbers (`0..127`), the streamer
  channel (`0..15`) and non-negative event ticks.

### Known limits

- AAF-XML does not serialise transitions. AAF `Transition` needs extension
  operation definitions; clips keep their record and source timing and
  `read_aafx` returns cuts.
- AAF-XML audio source slots use timeline frames, so `StartTime` round-trips
  but is not sample-accurate.
- Tick-based session placements are reported as ticks only. Converting them to
  samples requires the session tempo map, which is not decoded.

## [0.1.0a1] - 2026-10-19

### Added

- Initial alpha release.
- CMX 3600 EDL parsing and writing across four dialects, with round-trip
  stability and named errors for malformed input.
- Content-based file identification for EDL, AAF, WAV/BWF and session files.
- AAF interchange: simplified AAFX XML read/write, binary AAF export through
  the optional `pyaaf2` extra, and a dependency-free OLE2/CFB reader.
- BWF/WAV metadata including `bext` and `iXML`, plus timeline assembly from
  location recordings.
- Compare, re-conform planning, shooting-script import, delimited-text and
  column transforms, versioned change lists, and SMF type 1 MIDI writing.
- Private-session identification, decryption, block-tree walking, string and
  XML extraction, audio files and regions, and safe same-length name
  replacement.
- Tk desktop UI with a Tk-free view model and declarative command layer.
- CLI, library API, test suite and documentation.

[Unreleased]: https://github.com/hug0-l/conformkit/compare/v0.1.0a2...HEAD
[0.1.0a2]: https://github.com/hug0-l/conformkit/compare/v0.1.0a1...v0.1.0a2
[0.1.0a1]: https://github.com/hug0-l/conformkit/releases/tag/v0.1.0a1
