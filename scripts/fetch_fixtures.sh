#!/usr/bin/env bash
# Fetch the real-world EDL fixtures used by the validation and cross-check
# suites. They are not vendored because they come from a third-party MIT
# project, and keeping a single upstream source avoids drift.
set -euo pipefail

BASE="https://raw.githubusercontent.com/iluvcapra/pycmx/master/tests/edls"
DEST="$(dirname "$0")/../fixtures"

mkdir -p "$DEST"

FILES=(
  "TEST.edl"
  "test_25.edl"
  "test_24psf.edl"
  "ISSUE_19_unusual01.edl"
  "ISSUE_14_conform_edl_issue_01.edl"
  "ISSUE_14_conform_edl_issue_02.edl"
  "ISSUE_14_conform_edl_issue_03.edl"
  "cdl_example01.edl"
  "cdl_frmc_example01.edl"
  "cdl_frmc_example02.edl"
  "test_edl_cdl.edl"
)

for f in "${FILES[@]}"; do
  enc=$(python -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$f")
  out="$DEST/$f"
  if [ -s "$out" ]; then
    echo "have  $f"
    continue
  fi
  echo "fetch $f"
  curl -sSL --fail --max-time 60 -o "$out" "$BASE/$enc"
done

echo
echo "fixtures in $DEST:"
ls -la "$DEST" | tail -n +2

echo
echo "verify with:"
echo "  python -m pytest tests/test_real_edl.py tests/test_cross_pycmx.py -q"
