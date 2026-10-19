# Changelog

All notable changes to conformkit are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- AAFX output is moving from the private simplified XML schema to real
  AAF-XML. This work is in progress and is **not** part of the current branch;
  the shipped writer still emits the simplified schema.

## [0.1.0a2] - 2026-10-19

### Added

- Private-session region timing: the packed `sample_offset`, `length` and
  `start` triple is decoded with little-endian values, and each record carries
  a confidence rating. Validated against real audio, 100% of regions satisfy
  `sample_offset + length <= source length`; big-endian decoding satisfies only
  about 22%, which pins the byte order.
- Session audio-file metadata: sample rate, channel count, bit depth and
  length in samples, read from the `0x1001` records nested under `0x1003` and
  paired with names from `0x103A`.
- `--version` on the command line, including
  `python -m conformkit --version`.
- A single `conformkit.__version__` string, kept in sync with
  `pyproject.toml`.
- GitHub Actions test workflow for Python 3.10 to 3.13, including a summary
  of skipped tests so missing private fixtures stay visible.
- Packaging tests for version consistency and both CLI entry points.

### Changed

- AAF export creates one `SourceMob` per source **and media kind**, with its
  single slot sized to the furthest extent any clip reads. `SourceClip`
  objects now reference the matching mob id and slot id instead of a
  placeholder.
- MIDI note events can take their duration from the edit event length rather
  than a fixed frame count.
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
