#!/usr/bin/env python3
"""Validate an AAF-XML document against the AAF SDK reference schema.

The schema is downloaded on demand from the AAF reference implementation
(`dneg/aaf`, `ref-impl/xsd/aafxml`) into a temporary directory. Nothing is
vendored: the schema files remain under their own upstream terms.

    python scripts/validate_aafxml.py out.aafx
    python scripts/validate_aafxml.py out.aafx --schema-dir ./schema

Requires `lxml` (``pip install lxml``) and network access when no schema
directory is supplied. The unit tests never call this script, so ordinary
development and CI do not need either.

Exit status: 0 valid, 1 invalid, 2 missing dependency or download failure.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

__all__ = ["main"]

_BASE = ("https://raw.githubusercontent.com/dneg/aaf/master/"
         "ref-impl/xsd/aafxml")
_SCHEMA_FILES = {
    # aaf.xsd includes metadict.xsd and imports ns1.xsd by relative name, so
    # all three must land in the same directory.
    "aaf.xsd": f"{_BASE}/example/aaf.xsd",
    "ns1.xsd": f"{_BASE}/example/ns1.xsd",
    "metadict.xsd": f"{_BASE}/metadict.xsd",
}


def _download_schema(directory: Path) -> None:
    for name, url in _SCHEMA_FILES.items():
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                payload = response.read()
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(f"could not download {url}: {exc}") from exc
        (directory / name).write_bytes(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("document", help="AAF-XML file to validate")
    parser.add_argument(
        "--schema-dir", type=Path, default=None,
        help="directory containing aaf.xsd, ns1.xsd and metadict.xsd")
    args = parser.parse_args(argv)

    try:
        from lxml import etree
    except ImportError:
        print("validation needs lxml; install it with 'pip install lxml'",
              file=sys.stderr)
        return 2

    try:
        document = etree.parse(str(args.document))
    except (OSError, etree.XMLSyntaxError) as exc:
        print(f"{args.document}: {exc}", file=sys.stderr)
        return 1

    temporary = None
    try:
        if args.schema_dir is not None:
            schema_dir = args.schema_dir
            if not (schema_dir / "aaf.xsd").exists():
                print(f"{schema_dir}/aaf.xsd not found", file=sys.stderr)
                return 2
        else:
            temporary = tempfile.TemporaryDirectory(prefix="aafxml-schema-")
            schema_dir = Path(temporary.name)
            _download_schema(schema_dir)

        try:
            schema = etree.XMLSchema(etree.parse(str(schema_dir / "aaf.xsd")))
        except (OSError, etree.XMLSchemaParseError) as exc:
            print(f"could not load schema: {exc}", file=sys.stderr)
            return 2

        if schema.validate(document):
            print(f"{args.document}: valid AAF-XML v1.1")
            return 0

        for entry in schema.error_log:
            print(f"line {entry.line}: {entry.message}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
