"""Command-line interface.

    python -m conformkit info <file.edl>
    python -m conformkit convert <in.edl> <out.aafx> --rate 25
    python -m conformkit compare <old.edl> <new.edl> --rate 25
    python -m conformkit conform <session.edl> <newcut.edl> --rate 25
    python -m conformkit assemble <folder> --rate 25 --out assembly.edl
    python -m conformkit wavinfo <file.wav>
"""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .aaf import AAFError, AafContainer, identify_aaf, write_aafx
from .assembly import AssemblyError, build_assembly, most_common_rate, scan_wavs
from .bwf import WavError, derive_roll_name, derive_start_timecode, read_wav_info
from .cfb import CfbError
from .compare import compare_lists
from .conform import ConformError, plan_conform
from .edl import EDLError, detect_dialect, parse_edl, write_edl
from .ptx import PTXError, identify as identify_ptx, read_session
from .timecode import TCError, format_tc, rate_for, to_frames

RATE_CHOICES = ["23.976", "24", "25", "29.97", "29.97df", "30",
                "47.952", "48", "59.94", "59.94df", "60", "90"]


def _rate(args):
    return rate_for(args.rate, df=args.rate.endswith("df"))


def _read(path):
    with open(path, encoding=args_encoding(), errors="replace") as fh:
        return fh.read()


def args_encoding():
    return getattr(args_encoding, "enc", "utf-8")


def cmd_info(args):
    rate = _rate(args)
    text = _read(args.file)
    dialect = args.dialect or detect_dialect(text)
    el = parse_edl(text, rate, dialect, strict_roll_length=False)

    print(f"file        {args.file}")
    print(f"dialect     {dialect}")
    print(f"rate        {rate.label}{' DF' if rate.df else ''}")
    print(f"title       {el.title}")
    print(f"edits       {len(el)}")
    print(f"remarks     {len(el.remarks)}")
    print(f"warnings    {len(el.warnings)}")

    rolls = sorted({e.roll_name for e in el})
    print(f"rolls       {len(rolls)}")
    for r in rolls[:10]:
        print(f"              {r}")
    if len(rolls) > 10:
        print(f"              ... {len(rolls) - 10} more")

    tracks = sorted({t.label for e in el for t in e.tracks})
    print(f"tracks      {', '.join(tracks)}")

    if el.warnings:
        print("\nfirst warnings:")
        for w in el.warnings[:5]:
            print(f"  {w}")
    return 0


def cmd_identify(args):
    """Identify any supported file and report what was recognised."""
    with open(args.file, "rb") as fh:
        head = fh.read(65536)
    with open(args.file, "rb") as fh:
        whole = fh.read() if args.full else b""

    print(f"file        {args.file}")
    print(f"size        {os.path.getsize(args.file):,}")

    fmt = identify_ptx(whole or head, args.file)
    if fmt.is_pro_tools:
        print(f"format      Pro Tools session ({fmt.kind}), PRONOM {fmt.pronom}")
        print(f"confidence  {fmt.confidence}")
        if fmt.version_bytes:
            print(f"version     {fmt.version_bytes.hex(' ')}")
        if whole:
            s = read_session(args.file)
            print(f"session     {s.session_name}")
            print(f"user        {s.user_name or '-'}")
            print(f"host        {s.computer_name or '-'}")
            print(f"strings     {s.string_count}")
            print(f"xml docs    {len(s.xml_documents)}")
        return 0

    info = identify_aaf(whole or head)
    if info["is_aaf"]:
        print(f"format      AAF (binary, OLE2 container)")
        print(f"aaf sdk     {info['aaf_version']}")
        print(f"streams     {info['streams']}")
        print(f"SMPTE keys  {info['smpte_ul_count']:,}")
        print(f"looks ok    {info['looks_like_aaf']}")
        return 0

    dialect = detect_dialect(head.decode("utf-8", "replace"))
    if b"TITLE:" in head or b"FCM:" in head:
        print(f"format      EDL (text), dialect {dialect}")
        return 0

    if head[:4] == b"RIFF":
        print("format      WAV / BWF")
        return 0

    print("format      unrecognised")
    return 1


def cmd_ptx(args):
    """Summarise a Pro Tools session file."""
    s = read_session(args.file)
    print(f"file        {args.file}")
    print(f"format      {s.fmt.kind}  (PRONOM {s.fmt.pronom})")
    print(f"size        {s.size:,}")
    print(f"session     {s.session_name}")
    print(f"session file{s.session_file}")
    print(f"user        {s.user_name}")
    print(f"host        {s.computer_name}")
    print(f"sample rate {s.sample_rate or '-'}")
    print(f"xor         type=0x{s.xor_type:02x} value=0x{s.xor_value:02x}")
    print(f"blocks      {s.block_count:,}")
    print(f"strings     {s.string_count:,}")
    print(f"xml docs    {len(s.xml_documents):,}")

    if args.tracks:
        print(f"\ntracks ({len(s.track_names)}):")
        for name in s.track_names[:args.tracks]:
            print(f"  {name}")

    if args.clips:
        print(f"\nclips ({len(s.clip_names)}):")
        for name in s.clip_names[:args.clips]:
            print(f"  {name}")

    if args.plugins:
        print(f"\nplug-ins ({len(s.plugins)}):")
        for name in s.plugins[:args.plugins]:
            print(f"  {name}")

    if args.xml:
        for off, chunk, tag, was in s.xml_documents[:args.xml]:
            print(f"\n--- 0x{off:08x} <{tag}> ---")
            print(chunk.decode("ascii", "replace"))
    return 0


def cmd_aafinfo(args):
    """Summarise a binary AAF container."""
    c = AafContainer(args.file)
    info = c.info()
    print(f"file        {args.file}")
    print(f"format      AAF, SDK version {info['aaf_version']}")
    print(f"sector      {info['sector_size']}")
    print(f"streams     {info['streams']}")
    print(f"SMPTE keys  {info['smpte_ul_count']:,}")
    print("\nstreams:")
    for e in c.streams():
        print(f"  {e.size:>12,}  {e.name}")
    if args.names:
        names = c.names()
        print(f"\nnames ({len(names)}):")
        for n in names[:args.names]:
            print(f"  {n}")
    return 0


def cmd_convert(args):
    rate = _rate(args)
    text = _read(args.source)
    dialect = args.dialect or detect_dialect(text)
    el = parse_edl(text, rate, dialect, strict_roll_length=False)

    ext = os.path.splitext(args.dest)[1].lower()
    if ext in (".aafx", ".xml"):
        out = write_aafx(el, title=el.title or "Converted")
    elif ext == ".edl":
        out = write_edl(el, args.dialect or "CMX3600")
    else:
        print(f"unsupported output extension {ext!r}", file=sys.stderr)
        return 2

    with open(args.dest, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(out)
    print(f"wrote {args.dest}  ({len(el)} edits)")
    return 0


def cmd_compare(args):
    rate = _rate(args)
    old = parse_edl(_read(args.old), rate, "CMX3600", strict_roll_length=False)
    new = parse_edl(_read(args.new), rate, "CMX3600", strict_roll_length=False)
    cl = compare_lists(old, new)

    print(f"old         {args.old}  ({len(old)} edits)")
    print(f"new         {args.new}  ({len(new)} edits)")
    print(f"changes     {len(cl)}")
    for k, v in sorted(cl.counts().items()):
        print(f"  {k:<10} {v}")
    if cl.is_noisy():
        print("\nnote: most changes are +/-1 frame; consider an offset list")
    return 0


def cmd_conform(args):
    rate = _rate(args)
    old = parse_edl(_read(args.session), rate, "CMX3600", strict_roll_length=False)
    new = parse_edl(_read(args.newcut), rate, "CMX3600", strict_roll_length=False)
    try:
        plan = plan_conform(old, new, allow_new_material=args.allow_new)
    except ConformError as exc:
        print(f"cannot conform: {exc}", file=sys.stderr)
        return 1

    print(f"operations  {len(plan.ops)}")
    for k, v in sorted(plan.counts().items()):
        print(f"  {k:<10} {v}")
    print()
    for op in plan.ops:
        if op.kind.value == "keep":
            continue
        src = op.old.roll_name if op.old else "-"
        dst = op.new.roll_name if op.new else "-"
        print(f"  {op.kind.value:<8} {src:<12} -> {dst:<12} {op.delta:+5d}  {op.detail}")
    return 0


def cmd_assemble(args):
    rate = _rate(args)
    try:
        paths = scan_wavs(args.folder)
    except AssemblyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not paths:
        print("no WAV files found", file=sys.stderr)
        return 1

    infos = []
    for p in paths:
        try:
            infos.append(read_wav_info(path=p))
        except Exception:                               # noqa: BLE001
            pass
    print(f"wavs        {len(paths)}")
    print(f"common rate {most_common_rate(infos)} Hz")

    warnings = []
    result = build_assembly(paths, rate, group_by=args.group, warn=warnings)
    print(f"placed      {len(result.items)}")
    print(f"skipped     {len(result.skipped)}")
    for p, why in result.skipped[:5]:
        print(f"  {os.path.basename(p)}: {why}")
    for w in warnings[:5]:
        print(f"  warn: {w}")

    if args.out:
        el = result.to_edit_list()
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(write_edl(el, "CMX3600"))
        print(f"wrote {args.out}")
    return 0


def cmd_wavinfo(args):
    info = read_wav_info(path=args.file)
    print(f"file        {args.file}")
    print(f"channels    {info.channels}")
    print(f"sample rate {info.sample_rate} Hz")
    print(f"bits        {info.bits_per_sample}")
    print(f"frames      {info.duration_frames}")
    print(f"duration    {info.sample_duration:.3f} s")
    print(f"has audio   {info.has_channel_data}")
    print(f"bext        {bool(info.bext_originator or info.bext_time_reference)}")
    if info.bext_time_reference:
        print(f"  time ref  {info.bext_time_reference} samples")
        print(f"  date      {info.bext_date} {info.bext_time}")
    if info.ix_project or info.ix_tape:
        print(f"iXML        project={info.ix_project!r} scene={info.ix_scene!r} "
              f"take={info.ix_take!r} tape={info.ix_tape!r}")
    print(f"roll name   {derive_roll_name(info)!r}")
    return 0


def cmd_gui(args):
    """Launch the desktop window."""
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("error: tkinter is not available in this Python build",
              file=sys.stderr)
        return 1

    from .gui import main as gui_main

    argv = []
    if args.file:
        argv.append(args.file)
    if args.gui_rate:
        os.environ["CONFORMKIT_RATE"] = args.gui_rate
    sys.argv = ["conformkit-gui", *argv]
    return gui_main()


def build_parser():
    p = argparse.ArgumentParser(prog="conformkit", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {__version__}")
    p.add_argument("--encoding", default="utf-8",
                   help="text encoding for reading EDLs (default utf-8)")
    p.add_argument("--rate", default="25", choices=RATE_CHOICES,
                   help="frame rate (append 'df' for drop frame)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("info", help="summarise an edit list")
    s.add_argument("file")
    s.add_argument("--dialect", default=None)
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("convert", help="convert between EDL and AAF-XML")
    s.add_argument("source")
    s.add_argument("dest")
    s.add_argument("--dialect", default=None)
    s.set_defaults(func=cmd_convert)

    s = sub.add_parser("compare", help="diff two cuts")
    s.add_argument("old")
    s.add_argument("new")
    s.set_defaults(func=cmd_compare)

    s = sub.add_parser("conform", help="plan a re-conform")
    s.add_argument("session")
    s.add_argument("newcut")
    s.add_argument("--allow-new", action="store_true",
                   help="permit material absent from the session")
    s.set_defaults(func=cmd_conform)

    s = sub.add_parser("assemble", help="build a list from location WAVs")
    s.add_argument("folder")
    s.add_argument("--out", default=None)
    s.add_argument("--group", default="scene", choices=["scene", "roll"])
    s.set_defaults(func=cmd_assemble)

    s = sub.add_parser("wavinfo", help="dump BWF metadata")
    s.add_argument("file")
    s.set_defaults(func=cmd_wavinfo)

    s = sub.add_parser("identify", help="identify any supported file")
    s.add_argument("file")
    s.add_argument("--full", action="store_true",
                   help="read the whole file, not just the header")
    s.set_defaults(func=cmd_identify)

    s = sub.add_parser("ptx", help="summarise a Pro Tools session file")
    s.add_argument("file")
    s.add_argument("--tracks", type=int, default=0,
                   help="also print up to N track names")
    s.add_argument("--clips", type=int, default=0,
                   help="also print up to N clip names")
    s.add_argument("--plugins", type=int, default=0,
                   help="also print up to N plug-in names")
    s.add_argument("--xml", type=int, default=0,
                   help="dump up to N recovered XML documents")
    s.set_defaults(func=cmd_ptx)

    s = sub.add_parser("aafinfo", help="summarise a binary AAF container")
    s.add_argument("file")
    s.add_argument("--names", type=int, default=0,
                   help="also print up to N recovered names")
    s.set_defaults(func=cmd_aafinfo)

    s = sub.add_parser("gui", help="open the desktop window")
    s.add_argument("file", nargs="?", default=None,
                   help="file to open on start")
    s.add_argument("--rate", dest="gui_rate", default=None,
                   choices=RATE_CHOICES,
                   help="initial frame rate for edit lists")
    s.set_defaults(func=cmd_gui)
    return p


def main(argv=None):
    global args
    parser = build_parser()
    args = parser.parse_args(argv)
    args_encoding.enc = args.encoding
    try:
        return args.func(args)
    except (EDLError, TCError, AssemblyError, ConformError, WavError,
            AAFError, PTXError, CfbError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
