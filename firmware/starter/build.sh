#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Build the starter firmware: firmware/build/playground-starter.elf
# Usage: firmware/starter/build.sh [output.elf]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
WORK="${BEITLAB_WORK_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/beitlab-duo-os}"
OUT="${1:-${BEITLAB_BUILD_DIR:-$WORK/build}/firmware/playground-starter.elf}"
mkdir -p "$(dirname "$OUT")"
exec python3 "$REPO/firmware/rtosbuild.py" build --name starter \
  --version "$(cat "$REPO/VERSION")" --out "$OUT" "$HERE/starter.c"
