#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Build the vendor C906L FreeRTOS libraries the playground programs link.
#
# This runs the same CMake stages as freertos/cvitek/build_cv181x.sh with
# RUN_TYPE=CVIRTOS (arch, kernel, common, hal, driver), with the same
# toolchain file and the same -D options. It stops before the task stage:
# the playground runtime replaces task/comm, and each program supplies its
# own application. Output: <SDK>/freertos/cvitek/install/{lib,include}.
#
# It also produces what the SDK's build system would have produced before
# calling build_cv181x.sh: build/.config (Kconfig, from the board defconfig)
# and build/output/<board>/cvi_board_memmap.{h,ld,conf} (from memmap.py).
#
# Environment:
#   PLAYGROUND_SDK               SDK checkout (default ~/.cache/beitlab-duo-os/sdk)
#   PLAYGROUND_RISCV_TOOLCHAIN   bin/ of the riscv64-elf toolchain
#                                (default <sdk>/host-tools/gcc/riscv64-elf-x86_64/bin)
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
lock() { sed -n "s/^$1[[:space:]]*=[[:space:]]*//p" "$REPO/sdk.lock" | head -1; }

WORK="${BEITLAB_WORK_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/beitlab-duo-os}"
SDK="${PLAYGROUND_SDK:-$WORK/sdk}"
BOARD="$(lock board_link)"
TC_DEFAULT="$SDK/host-tools/$(lock host_tools_riscv64_elf)/bin"
[ -f "$SDK/PLAYGROUND_TOOLCHAIN" ] && TC_DEFAULT="$(cat "$SDK/PLAYGROUND_TOOLCHAIN")"
TC="${PLAYGROUND_RISCV_TOOLCHAIN:-$TC_DEFAULT}"
TOP="$SDK/freertos/cvitek"

die() { echo "build-rtos-libs: $*" >&2; exit 1; }

[ -f "$TOP/build_cv181x.sh" ] || die "$TOP is missing; run scripts/fetch-freertos.sh"
[ -x "$TC/riscv64-unknown-elf-gcc" ] || die "no riscv64-unknown-elf-gcc in $TC; run scripts/fetch-freertos.sh"
want="$(lock toolchain_file_sha256)"
got="$(sha256sum "$SDK/$(lock toolchain_file)" | cut -d' ' -f1)"
[ "$want" = "$got" ] || die "toolchain file hash $got != sdk.lock $want"
command -v cmake >/dev/null || die "cmake is required"

export PATH="$TC:$PATH"
export TOP_DIR="$SDK" BUILD_PATH="$SDK/build" PROJECT_FULLNAME="$BOARD"

# ---- .config and memory map, as build/envsetup_milkv.sh + mmap.mk do ----
(
  cd "$BUILD_PATH"
  python3 scripts/boards_scan.py --gen-build-kconfig
  python3 scripts/gen_sensor_config.py
  python3 scripts/gen_panel_config.py
  python3 scripts/defconfig.py --kconfig Kconfig \
    "boards/cv181x/$BOARD/${BOARD}_defconfig" >/dev/null
  mkdir -p "output/$BOARD"
  for t in h ld conf; do
    python3 scripts/mmap_conv.py --type "$t" "boards/cv181x/$BOARD/memmap.py" \
      "output/$BOARD/cvi_board_memmap.$t" 2>/dev/null
  done
)
grep -q '^CONFIG_CHIP_ARCH_cv181x=y' "$BUILD_PATH/.config" || die "generated .config is not cv181x"
grep -q '^CONFIG_ENABLE_FREERTOS=y' "$BUILD_PATH/.config" || die "board defconfig lost CONFIG_ENABLE_FREERTOS=y"
# The SDK exports DDR_64MB_SIZE from .config (boards_scan.py gen_build_env,
# kconfiglib str_value: "y" or "n"). toolchain-riscv64-elf.cmake tests
# $ENV{DDR_64MB_SIZE} and is a CMake error when it is unset.
if grep -q '^CONFIG_DDR_64MB_SIZE=y' "$BUILD_PATH/.config"; then
  export DDR_64MB_SIZE=y
else
  export DDR_64MB_SIZE=n
fi

# ---- vendor stages (build_cv181x.sh, RUN_TYPE=CVIRTOS, minus task) ----
GEN="Unix Makefiles"
command -v ninja >/dev/null && GEN="Ninja"   # build_cv181x.sh uses Ninja
TOOLCHAIN_FILE_PATH="$TOP/scripts/toolchain-riscv64-elf.cmake"
BUILD_FREERTOS_PATH="$TOP/build"
RUN_TYPE=CVIRTOS RUN_CHIP=cv181x RUN_ARCH=riscv64

stage() {  # stage <name> <source dir> [extra -D...]
  local name="$1" src="$2"; shift 2
  mkdir -p "$BUILD_FREERTOS_PATH/$name"
  ( cd "$BUILD_FREERTOS_PATH/$name"
    cmake -G "$GEN" -DCHIP=$RUN_CHIP -DRUN_ARCH=$RUN_ARCH -DTOP_DIR="$TOP" \
      -DCMAKE_TOOLCHAIN_FILE="$TOOLCHAIN_FILE_PATH" "$@" "$src" >/dev/null
    cmake --build . --target install >/dev/null )
  echo "build-rtos-libs: $name ok"
}

stage arch   "$TOP/arch"        -DRUN_TYPE=$RUN_TYPE
stage kernel "$TOP/kernel"
stage common "$TOP/common"      -DBUILD_ENV_PATH="$BUILD_PATH"
stage hal    "$TOP/hal/$RUN_CHIP" -DRUN_TYPE=$RUN_TYPE -DBOARD_FPGA=n
stage driver "$TOP/driver"      -DRUN_TYPE=$RUN_TYPE -DBUILD_ENV_PATH="$BUILD_PATH" -DBOARD_FPGA=n

for l in arch kernel common hal driver; do
  [ -f "$TOP/install/lib/lib$l.a" ] || die "lib$l.a was not installed"
done
echo "build-rtos-libs: libraries in $TOP/install/lib"
