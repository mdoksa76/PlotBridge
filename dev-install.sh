#!/usr/bin/env bash
# Plot Bridge — dev install cycle.
# Usage: run from the folder that contains the 'plotbridge/' source dir.
#   ./dev-install.sh           # rebuild .oxt, reinstall, relaunch Writer
#   ./dev-install.sh --nolaunch
set -e

SRC="plotbridge"
OXT="PlotBridge.oxt"
ID="org.mdoksa.plotbridge"

if [ ! -d "$SRC" ]; then
  echo "error: '$SRC/' source dir not found (run from its parent)."; exit 1
fi

echo ">> packing $OXT"
rm -f "$OXT"
( cd "$SRC" && zip -r -q "../$OXT" . -x ".*" "*__pycache__*" )

echo ">> removing old extension (ignore error if not installed)"
unopkg remove "$ID" 2>/dev/null || true

echo ">> installing"
unopkg add -f "$OXT"

if [ "$1" != "--nolaunch" ]; then
  echo ">> launching LibreOffice Writer"
  # -norestore avoids the recovery dialog eating focus during dev
  soffice --writer --norestore &
fi

echo ">> done. Tools menu -> 'Plot Bridge...'"
