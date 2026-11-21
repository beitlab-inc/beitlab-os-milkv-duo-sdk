#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Prepare a PC to build the PlatformIO examples. Run once, from anywhere:
#   scripts/fetch-freertos.sh
#
# Fills the external SDK cache with a sparse checkout of the Milk-V SDK at the sdk.lock
# commit, limited to the FreeRTOS tree the examples link and the SDK
# metadata the FreeRTOS CMake needs, and fetches the C906 toolchain:
#
#   freertos/cvitek       vendor FreeRTOS port, drivers, build_cv181x.sh
#   freertos/Source       FreeRTOS kernel (cvitek/kernel compiles ../Source)
#   freertos/Tracealyzer  (cvitek/kernel compiles ../Tracealyzer/src)
#   build/Kconfig, build/scripts, build/boards, build/panels, build/sensors
#                         board defconfig -> .config, memmap.py -> memmap.ld
#
# Toolchain: the SDK documents no standalone tarball for its riscv64-elf
# toolchain; build.sh get_toolchain clones github.com/milkv-duo/host-tools.
# This script uses that host-tools tree at the sdk.lock commit, checks out
# only gcc/riscv64-elf-x86_64, and records the path in
# sdk/PLAYGROUND_TOOLCHAIN. It is an x86_64 Linux toolchain: on Windows use
# WSL2, on macOS use a Linux VM or Docker. To use a copy you already have,
# set PLAYGROUND_RISCV_TOOLCHAIN=<that toolchain>/bin and pass --no-toolchain.
#
# Then it builds the vendor FreeRTOS libraries (scripts/build-rtos-libs.sh).
# Nothing here needs the rest of the SDK, Buildroot, or root.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
lock() { sed -n "s/^$1[[:space:]]*=[[:space:]]*//p" "$REPO/sdk.lock" | head -1; }
die() { echo "fetch-freertos: $*" >&2; exit 1; }
say() { echo "fetch-freertos: $*"; }

FETCH_TC=1
for a in "$@"; do
  case "$a" in
    --no-toolchain) FETCH_TC=0 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) die "unknown option $a" ;;
  esac
done

WORK="${BEITLAB_WORK_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/beitlab-duo-os}"
SDK="${PLAYGROUND_SDK:-$WORK/sdk}"
URL="$(lock sdk_url)"; COMMIT="$(lock sdk_commit)"
HT_URL="$(lock host_tools_url)"; HT_COMMIT="$(lock host_tools_commit)"
HT_RV="$(lock host_tools_riscv64_elf)"
command -v git >/dev/null || die "git is required"
command -v cmake >/dev/null || die "cmake is required (apt install cmake / brew install cmake)"
command -v python3 >/dev/null || die "python3 is required"

PATTERNS=(/freertos/cvitek/ /freertos/Source/ /freertos/Tracealyzer/
          /build/Kconfig /build/scripts/ /build/boards/ /build/panels/ /build/sensors/)

# ---- SDK sparse checkout ----
if [ ! -d "$SDK/.git" ]; then
  say "cloning $URL (blobless, sparse) into $SDK"
  git clone --filter=blob:none --no-checkout "$URL" "$SDK"
  git -C "$SDK" sparse-checkout init --no-cone
  git -C "$SDK" sparse-checkout set "${PATTERNS[@]}"
elif [ "$(git -C "$SDK" config --get core.sparseCheckout || echo false)" = true ]; then
  git -C "$SDK" sparse-checkout add "${PATTERNS[@]}"
fi
if ! git -C "$SDK" cat-file -e "$COMMIT^{commit}" 2>/dev/null; then
  git -C "$SDK" fetch --filter=blob:none origin "$COMMIT"
fi
# clone --no-checkout can already have HEAD at the pin with no working
# files/index. Always materialize the checkout, including after interruption.
git -C "$SDK" -c advice.detachedHead=false checkout --detach "$COMMIT"
[ "$(git -C "$SDK" rev-parse HEAD)" = "$COMMIT" ] || die "sdk/ is not at $COMMIT"

got="$(sha256sum "$SDK/$(lock toolchain_file)" | cut -d' ' -f1)"
[ "$got" = "$(lock toolchain_file_sha256)" ] || die "$(lock toolchain_file) hash $got does not match sdk.lock"
got="$(sha256sum "$SDK/$(lock cmdqu_header)" | cut -d' ' -f1)"
[ "$got" = "$(lock cmdqu_header_sha256)" ] || die "$(lock cmdqu_header) hash $got does not match sdk.lock"
say "SDK FreeRTOS tree at $COMMIT"

# ---- toolchain ----
if [ "$FETCH_TC" = 1 ]; then
  case "$(uname -s)-$(uname -m)" in
    Linux-x86_64) ;;
    *) die "the SDK riscv64-elf toolchain is x86_64 Linux only; run this in WSL2/Linux, or
  set PLAYGROUND_RISCV_TOOLCHAIN to a toolchain bin/ and pass --no-toolchain" ;;
  esac
  HT="$SDK/host-tools"
  if [ ! -d "$HT/.git" ]; then
    say "fetching $HT_RV from $HT_URL (sparse)"
    git clone --filter=blob:none --no-checkout "$HT_URL" "$HT"
    git -C "$HT" sparse-checkout init --no-cone
    git -C "$HT" sparse-checkout set "/$HT_RV/"
  elif [ "$(git -C "$HT" config --get core.sparseCheckout || echo false)" = true ]; then
    git -C "$HT" sparse-checkout add "/$HT_RV/"
  fi
  git -C "$HT" cat-file -e "$HT_COMMIT^{commit}" 2>/dev/null || git -C "$HT" fetch --filter=blob:none origin "$HT_COMMIT"
  git -C "$HT" -c advice.detachedHead=false checkout --detach "$HT_COMMIT"
  TC="$HT/$HT_RV/bin"
else
  TC="${PLAYGROUND_RISCV_TOOLCHAIN:-}"
  [ -n "$TC" ] || die "--no-toolchain needs PLAYGROUND_RISCV_TOOLCHAIN=<toolchain>/bin"
fi
[ -x "$TC/riscv64-unknown-elf-gcc" ] || die "no riscv64-unknown-elf-gcc in $TC"
echo "$TC" > "$SDK/PLAYGROUND_TOOLCHAIN"
say "C906 toolchain: $TC"
say "  $("$TC/riscv64-unknown-elf-gcc" --version | head -1)"
say "  (path recorded in $SDK/PLAYGROUND_TOOLCHAIN)"

# ---- vendor FreeRTOS libraries ----
PLAYGROUND_SDK="$SDK" PLAYGROUND_RISCV_TOOLCHAIN="$TC" "$REPO/scripts/build-rtos-libs.sh"
say "done. Next: cd examples/platformio/hello && pio run -t upload"
