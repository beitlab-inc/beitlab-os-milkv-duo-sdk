#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Build the beitlab Robotics MilkV duo OS SD card image.
#
#   scripts/build-image.sh            on Ubuntu 22.04 amd64 (SDK requirement)
#   scripts/build-image.sh --docker   anywhere with Docker: runs this script
#                                     in the SDK's documented build container
#   --standalone-only                 skip the derived Robotics Board image
#
# Output (PROMPT.md 0.5.0, "Two images from one build"):
#   $BEITLAB_OUTPUT_DIR/beitlab-robotics-milkv-duo-os-<version>-duos-standalone-arm64-sd.img
#   $BEITLAB_OUTPUT_DIR/beitlab-robotics-milkv-duo-os-<version>-robotics-board-arm64-sd.img
#   $BEITLAB_OUTPUT_DIR/rtos-agent-<version>-aarch64            (the agent binary in the image)
#   $BEITLAB_OUTPUT_DIR/SHA256SUMS                              (all of the above)
# The SDK builds once (standalone); scripts/make-variant.py derives the Robotics
# Board image from it by rewriting only the variant files.
#   $BEITLAB_OUTPUT_DIR/dtb-check.txt, build-checks.txt     (post-build inspection)
#
# Steps: read sdk.lock -> full SDK checkout at sdk_commit -> host-tools at
# host_tools_commit -> check the board defconfig -> apply image/patches ->
# build the C906 libraries, starter firmware, rtos-agent and host_ping ->
# install the overlay -> ./build.sh milkv-duos-glibc-arm64-sd -> verify the
# kernel config, dtb and rootfs -> copy the image to the external release directory.
#
# Needs network (SDK, host-tools, Buildroot sources, the Bootlin toolchain)
# and ~40 GB of disk. A full build takes 1-3 hours.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
lock() { sed -n "s/^$1[[:space:]]*=[[:space:]]*//p" "$REPO/sdk.lock" | head -1; }
die() { echo "build-image: error: $*" >&2; exit 1; }
say() { echo "build-image: $*"; }

DOCKER=0 IN_DOCKER=0 ROBOTICS=1
for a in "$@"; do
  case "$a" in
    --docker) DOCKER=1 ;;
    --in-docker) IN_DOCKER=1 ;;
    --standalone-only) ROBOTICS=0 ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) die "unknown option $a" ;;
  esac
done

VERSION="$(cat "$REPO/VERSION")"
TARGET="$(lock target)"; BOARD="$(lock board_link)"
URL="$(lock sdk_url)"; COMMIT="$(lock sdk_commit)"
WORK="${BEITLAB_WORK_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/beitlab-duo-os}"
SDK="${PLAYGROUND_SDK:-$WORK/sdk}"
OUT="${BEITLAB_OUTPUT_DIR:-$WORK/releases/$VERSION}"
BUILD="${BEITLAB_BUILD_DIR:-$WORK/build}"
export PYTHONDONTWRITEBYTECODE=1

# ---- host ----
if [ "$DOCKER" = 1 ]; then
  command -v docker >/dev/null || die "--docker needs docker"
  IMG="$(lock docker_image)"
  say "running in $IMG"
  # The SDK, output and build folders are mounted at their host paths: CMake caches
  # (FreeRTOS libraries, also used by host PlatformIO builds) record absolute
  # paths and refuse a different mount point.
  # The SDK container runs as root (Milk-V's documented usage). GNU
  # autoconf packages Buildroot builds (host-tar, coreutils) refuse to
  # configure as root unless FORCE_UNSAFE_CONFIGURE=1.
  mkdir -p "$SDK" "$OUT" "$BUILD"
  exec docker run --rm -v "$REPO:/home/work" -w /home/work \
    -v "$SDK:$SDK" -v "$OUT:$OUT" -v "$BUILD:$BUILD" \
    -e PLAYGROUND_SDK="$SDK" -e BEITLAB_OUTPUT_DIR="$OUT" \
    -e BEITLAB_BUILD_DIR="$BUILD" -e PYTHONDONTWRITEBYTECODE=1 \
    -e FORCE_UNSAFE_CONFIGURE=1 \
    -e PLAYGROUND_README_URL="${PLAYGROUND_README_URL:-}" \
    -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
    "$IMG" /home/work/scripts/build-image.sh --in-docker $([ "$ROBOTICS" = 0 ] && echo --standalone-only)
fi
if [ "$IN_DOCKER" = 1 ]; then
  # The bind-mounted repo belongs to the host user; let git use it, and
  # hand the generated files back to that user when we exit.
  git config --global --add safe.directory '*'
  if [ -n "${HOST_UID:-}" ]; then
    trap 'chown -R "$HOST_UID:${HOST_GID:-$HOST_UID}" "$SDK" "$OUT" "$BUILD" 2>/dev/null || true' EXIT
  fi
fi
# Read os-release in a subshell: it defines VERSION, which is ours.
osr() { ( . /etc/os-release; eval "echo \"\${$1:-}\"" ); }
HOST_DESC="$(osr PRETTY_NAME) $(uname -m)$([ "$IN_DOCKER" = 1 ] && echo " (in $(lock docker_image))")"
if [ "$IN_DOCKER" = 0 ] && { [ "$(osr ID)" != ubuntu ] || [ "$(osr VERSION_ID)" != 22.04 ] || [ "$(uname -m)" != x86_64 ]; }; then
  die "the SDK supports Ubuntu 22.04 amd64 only (this is $HOST_DESC).
  Use: scripts/build-image.sh --docker"
fi
say "host: $HOST_DESC"

# ---- full SDK checkout at the pinned commit ----
if [ ! -d "$SDK/.git" ]; then
  say "cloning $URL"
  git clone --filter=blob:none --no-checkout "$URL" "$SDK"
fi
if [ "$(git -C "$SDK" config --get core.sparseCheckout || echo false)" = true ]; then
  say "sdk/ is a sparse checkout (fetch-freertos.sh); widening to the full tree"
  git -C "$SDK" sparse-checkout disable
fi
git -C "$SDK" cat-file -e "$COMMIT^{commit}" 2>/dev/null || git -C "$SDK" fetch origin "$COMMIT"
git -C "$SDK" -c advice.detachedHead=false checkout --detach "$COMMIT"
[ "$(git -C "$SDK" rev-parse HEAD)" = "$COMMIT" ] || die "sdk/ is not at $COMMIT"

got="$(sha256sum "$SDK/$(lock toolchain_file)" | cut -d' ' -f1)"
[ "$got" = "$(lock toolchain_file_sha256)" ] || die "toolchain file hash $got != sdk.lock"

DEFCONFIG="$SDK/build/boards/cv181x/$BOARD/${BOARD}_defconfig"
for line in CONFIG_ENABLE_FREERTOS=y CONFIG_ENABLE_RTOS_DUMP_PRINT=y; do
  grep -qx "$line" "$DEFCONFIG" || die "$line missing from $DEFCONFIG at $COMMIT"
done
say "board defconfig has CONFIG_ENABLE_FREERTOS=y and CONFIG_ENABLE_RTOS_DUMP_PRINT=y"

# ---- host-tools at the pinned commit (build.sh would clone HEAD) ----
HT="$SDK/host-tools"; HT_COMMIT="$(lock host_tools_commit)"
if [ ! -d "$HT/.git" ]; then
  say "cloning $(lock host_tools_url)"
  git clone --filter=blob:none --no-checkout "$(lock host_tools_url)" "$HT"
fi
if [ "$(git -C "$HT" config --get core.sparseCheckout || echo false)" = true ]; then
  git -C "$HT" sparse-checkout disable
fi
git -C "$HT" cat-file -e "$HT_COMMIT^{commit}" 2>/dev/null || git -C "$HT" fetch origin "$HT_COMMIT"
git -C "$HT" -c advice.detachedHead=false checkout --detach "$HT_COMMIT"
AARCH64_CC="$HT/$(lock host_tools_aarch64_linux)/bin/aarch64-linux-gnu-gcc"
[ -x "$AARCH64_CC" ] || die "$AARCH64_CC missing"
rm -f "$SDK/PLAYGROUND_TOOLCHAIN"   # use the full host-tools tree

# ---- patches ----
# Patches stack (0002 and 0007 edit lines 0001 adds), so a per-patch
# "already applied?" test cannot work. Instead restore every file the
# series touches to sdk_commit, then apply the whole series in order.
for f in $(sed -n 's#^+++ b/##p; s#^--- a/##p' "$REPO"/image/patches/*.patch | sort -u); do
  if git -C "$SDK" cat-file -e "$COMMIT:$f" 2>/dev/null; then
    git -C "$SDK" checkout "$COMMIT" -- "$f"
  else
    rm -f "$SDK/$f"   # created by a patch
  fi
done
for p in "$REPO"/image/patches/*.patch; do
  git -C "$SDK" apply "$p" || die "$(basename "$p") does not apply to $COMMIT"
  say "patch $(basename "$p") applied"
done
cp "$REPO/image/dts/playground_duos.dtsi" "$SDK/build/boards/cv181x/$BOARD/dts_arm64/"

# ---- C906 libraries, starter firmware ----
PLAYGROUND_SDK="$SDK" "$REPO/scripts/build-rtos-libs.sh"
MEMMAP_H="$SDK/build/output/$BOARD/cvi_board_memmap.h"
mm() { sed -n "s/^#define $1 \(0x[0-9a-fA-F]*\).*/\1/p" "$MEMMAP_H"; }
RTOS_BASE="$(mm CVIMMAP_FREERTOS_ADDR)"; RTOS_SIZE="$(mm CVIMMAP_FREERTOS_SIZE)"
[ -n "$RTOS_BASE" ] && [ -n "$RTOS_SIZE" ] || die "memmap values missing in $MEMMAP_H"

FW_BUILD="$BUILD/firmware"
mkdir -p "$FW_BUILD"
PLAYGROUND_SDK="$SDK" "$REPO/firmware/starter/build.sh" "$FW_BUILD/playground-starter.elf"
if [ "$ROBOTICS" = 1 ]; then
  [ -x "$REPO/firmware/inspector/build.sh" ] || die "firmware/inspector is missing; use --standalone-only"
  PLAYGROUND_SDK="$SDK" "$REPO/firmware/inspector/build.sh" "$FW_BUILD/board-inspector.elf"
fi
if git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
  STARTER_DESC="$(git -C "$REPO" describe --always --dirty --tags 2>/dev/null)"
else
  STARTER_DESC="nogit-$(cat "$REPO"/firmware/starter/*.c "$REPO"/firmware/runtime/*.c \
                 "$REPO"/firmware/include/*.h | sha256sum | cut -c1-12)"
fi

# ---- Linux tools (aarch64, static, SDK host-tools gcc) ----
make -C "$REPO/agent" B="$BUILD/agent" clean >/dev/null
make -C "$REPO/agent" B="$BUILD/agent" CROSS_COMPILE="${AARCH64_CC%gcc}" MEMMAP_H="$MEMMAP_H"
make -C "$REPO/agent" B="$BUILD/agent" test MEMMAP_H="$MEMMAP_H"   # host build of the same sources
"$AARCH64_CC" -O2 -Wall -static -o "$FW_BUILD/host_ping" \
  "$REPO/examples/platformio/mailbox_ping/host_ping.c"
"$AARCH64_CC" -O2 -Wall -Wextra -static -o "$FW_BUILD/bl_send" \
  "$REPO/examples/platformio/beitlabduino_mailbox/linux/bl_send.c"

# ---- overlay ----
OV="$SDK/device/$TARGET/overlay"
rm -rf "$OV"; mkdir -p "$OV"
cp -a "$REPO/image/overlay/." "$OV/"
install -m 0644 "$REPO/image/permissions.txt" "$SDK/device/$TARGET/permissions.txt"
install -m 0644 "$REPO/image/users.txt" "$SDK/device/$TARGET/users.txt"
install -m 0644 "$REPO/image/busybox-user.config" "$SDK/device/$TARGET/busybox-user.config"
install -D -m 0755 "$BUILD/agent/rtos-agent" "$OV/usr/bin/rtos-agent"
install -D -m 0755 "$FW_BUILD/host_ping" "$OV/usr/bin/host_ping"
install -D -m 0755 "$FW_BUILD/bl_send" "$OV/usr/bin/bl_send"
install -D -m 0755 "$REPO/link/beitlab-link.py" "$OV/usr/bin/beitlab-link"

README_URL="${PLAYGROUND_README_URL:-}"
if [ -z "$README_URL" ] && remote="$(git -C "$REPO" remote get-url origin 2>/dev/null)"; then
  README_URL="$(echo "$remote" | sed -E 's#^git@([^:]+):#https://\1/#; s#\.git$##')#readme"
fi
if [ -z "$README_URL" ]; then
  README_URL="README.md of the duo-s-playground repository"
  say "warning: no git remote and no PLAYGROUND_README_URL; MOTD names the README without a URL"
fi
# Release identity, starter and board files of the standalone variant; the
# Robotics Board image gets its own from the same script (make-variant.py derive).
VARIANT_ARGS=(--version "$VERSION" --sdk-url "$URL" --sdk-commit "$COMMIT" --sdk-target "$TARGET"
              --starter-desc "$STARTER_DESC" --readme "$README_URL")
python3 "$REPO/scripts/make-variant.py" overlay --variant standalone --dir "$OV" \
  --starter "$FW_BUILD/playground-starter.elf" "${VARIANT_ARGS[@]}"
echo beitlab-milkv-duo > "$OV/etc/hostname"
chmod 0600 "$OV/etc/wpa_supplicant/beitlab-wlan0.conf"

# ---- SDK build ----
say "running ./build.sh $TARGET (this takes a while)"
( cd "$SDK" && ./build.sh "$TARGET" )

# ---- post-build checks: a green build without the driver is a failure ----
CHECKS="$OUT/build-checks.txt"; mkdir -p "$OUT"; : > "$CHECKS"
check() { echo "$*" | tee -a "$CHECKS"; }
KOUT="$SDK/linux_5.10/build/$BOARD"
KCONF="$KOUT/.config"
[ -f "$KCONF" ] || die "kernel config $KCONF not found"
grep -qx 'CONFIG_REMOTEPROC=y' "$KCONF" || die "CONFIG_REMOTEPROC=y missing from $KCONF"
grep -qx 'CONFIG_CVITEK_REMOTEPROC=m' "$KCONF" || die "CONFIG_CVITEK_REMOTEPROC=m missing from $KCONF"
grep -qx 'CONFIG_CGROUPS=y' "$KCONF" || die "CONFIG_CGROUPS=y missing from $KCONF"
check "ok: $KCONF has CONFIG_REMOTEPROC=y CONFIG_CVITEK_REMOTEPROC=m CONFIG_CGROUPS=y"

DTB="$(find "$KOUT/arch/arm64/boot/dts" -name "$BOARD.dtb" | head -1)"
[ -f "$DTB" ] || die "$BOARD.dtb not found under $KOUT"
DTC="$KOUT/scripts/dtc/dtc"; [ -x "$DTC" ] || DTC="$(command -v dtc || true)"
[ -x "$DTC" ] || die "no dtc to inspect $DTB"
"$DTC" -I dtb -O dts -o "$OUT/$BOARD.dts" "$DTB" 2>/dev/null
CARRIER="$REPO/../Balanced_robot/include/balance/board_carrier.h"
CARGS=(--carrier-profile "$REPO/profiles/beitlab_robotics_rev011.json")
[ ! -f "$CARRIER" ] || CARGS+=(--carrier "$CARRIER")
python3 "$REPO/scripts/check-dtb.py" "$OUT/$BOARD.dts" "${CARGS[@]}" \
  --base "$RTOS_BASE" --size "$RTOS_SIZE" > "$OUT/dtb-check.txt" || {
  cat "$OUT/dtb-check.txt"; die "dtb check failed (remoteproc0 node or carrier pads)"; }
check "ok: $DTB passes scripts/check-dtb.py ($OUT/dtb-check.txt), sha256 $(sha256sum "$DTB" | cut -c1-16)"

# Inspect the rootfs tarball the SDK unpacks into the image (sd_image), not
# output/target: Buildroot runs `systemctl preset-all` on a copy while it
# creates the filesystem images.
TARBALL="$SDK/buildroot/output/$TARGET/images/rootfs.tar.xz"
[ -f "$TARBALL" ] || die "$TARBALL not found"
LIST="$OUT/rootfs-list.txt"
tar -tJf "$TARBALL" | sed 's#^\./##' > "$LIST"
for f in mnt/system/ko/cvitek_remoteproc.ko mnt/system/ko/cv181x_rtos_cmdqu.ko \
         mnt/system/ko/cv181x_fast_image.ko usr/bin/rtos-agent usr/bin/host_ping \
         usr/lib/firmware/playground.elf usr/lib/firmware/playground-starter.elf \
         usr/bin/bl_send usr/bin/python3 usr/bin/beitlab-link \
         etc/duo-playground-release usr/lib/systemd/system/rtos-agent.service \
         etc/systemd/system/multi-user.target.wants/rtos-agent.service \
         etc/systemd/system/multi-user.target.wants/sshd.service \
         etc/systemd/system/multi-user.target.wants/avahi-daemon.service \
         usr/sbin/sshd usr/sbin/avahi-daemon usr/libexec/sftp-server; do
  grep -qx "$f" "$LIST" || die "rootfs is missing /$f (or the unit is not enabled)"
done
[ "$(tar -xJOf "$TARBALL" ./etc/hostname)" = beitlab-milkv-duo ] || die "hostname is not beitlab-milkv-duo"
tar -xJOf "$TARBALL" ./etc/shadow | grep -q '^root:[!*]' || die "root password is not locked"
python3 "$REPO/scripts/check-rootfs-security.py" "$TARBALL" | tee -a "$CHECKS"
BUSYBOX_VERSION="$(sed -n 's/^BUSYBOX_VERSION = //p' "$SDK/buildroot/package/busybox/busybox.mk" | head -1)"
BUSYBOX_CONFIG="$SDK/buildroot/output/$TARGET/build/busybox-$BUSYBOX_VERSION/.config"
grep -qx 'CONFIG_CHPASSWD=y' "$BUSYBOX_CONFIG" || die "BusyBox chpasswd is missing"
grep -qx 'CONFIG_FEATURE_DEFAULT_PASSWD_ALGO="sha512"' "$BUSYBOX_CONFIG" || die "password changes must default to SHA-512"
check "ok: BusyBox includes chpasswd and defaults password changes to SHA-512"
check "ok: rootfs has the remoteproc module, agent, firmware, sshd, avahi; rtos-agent.service enabled"

# Wireless payload, boot ordering, saved-profile permissions and identity.
python3 "$REPO/scripts/check-rootfs-network.py" "$TARBALL" "$VERSION" | tee -a "$CHECKS"
python3 "$REPO/scripts/check-rootfs-studio.py" "$TARBALL" duos_standalone | tee -a "$CHECKS"

# ---- output ----
IMG="$(ls -t "$SDK"/out/"$TARGET"_*.img 2>/dev/null | head -1 || true)"
[ -f "$IMG" ] || die "no $TARGET image in $SDK/out"

boot_checks() {  # image, expected board
  local image="$1" board="$2" offset drive
  offset=$(( $(od -An -tu4 -j454 -N4 "$image") * 512 ))
  drive="$image@@$offset"
  mcopy -i "$drive" ::/playground/wifi-supported - |
    grep -qx 'beitlab boot Wi-Fi provisioning v1' || die "boot Wi-Fi provisioning marker missing"
  mcopy -i "$drive" ::/playground/setup-supported - |
    grep -qx 'beitlab first-boot Wi-Fi, hostname and SSH public-key provisioning v1' || die "first-boot setup marker missing"
  mcopy -i "$drive" ::/playground/user-supported - |
    grep -qx 'beitlab regular-user provisioning v1' || die "regular-user marker missing"
  mcopy -i "$drive" ::/playground/studio-supported - |
    grep -qx 'beitlab Studio link provisioning v1' || die "Studio marker missing"
  mcopy -i "$drive" ::/playground/board - | grep -qx "$board" || die "boot board marker is not $board"
  for f in ::/fip.bin ::/boot.sd; do
    mdir -i "$drive" "$f" >/dev/null || die "boot volume missing $f"
  done
  for f in wifi.conf hostname authorized_keys user.conf studio.conf device-secret; do
    if mdir -i "$drive" "::/playground/$f" >/dev/null 2>&1; then
      die "release image contains personal boot settings: $f"
    fi
  done
  check "ok: $(basename "$image"): boot volume has fip.bin, boot.sd, all setup markers, board $board; no personal settings"
}

NAMES=()
NAME="$(python3 "$REPO/scripts/make-variant.py" name --variant standalone --version "$VERSION")"
cp "$IMG" "$OUT/$NAME"
boot_checks "$OUT/$NAME" duos_standalone
NAMES+=("$NAME")

if [ "$ROBOTICS" = 1 ]; then
  RNAME="$(python3 "$REPO/scripts/make-variant.py" name --variant robotics-board --version "$VERSION")"
  RTAR="$BUILD/rootfs-robotics-board.tar.xz"
  rm -f "$OUT/$RNAME" "$RTAR"
  python3 "$REPO/scripts/make-variant.py" derive --variant robotics-board \
    --image "$OUT/$NAME" --out "$OUT/$RNAME" --tarball "$TARBALL" --out-tarball "$RTAR" \
    --starter "$FW_BUILD/board-inspector.elf" "${VARIANT_ARGS[@]}"
  python3 "$REPO/scripts/check-rootfs-security.py" "$RTAR" | tee -a "$CHECKS"
  python3 "$REPO/scripts/check-rootfs-network.py" "$RTAR" "$VERSION" | tee -a "$CHECKS"
  python3 "$REPO/scripts/check-rootfs-studio.py" "$RTAR" robotics_board_rev011 | tee -a "$CHECKS"
  boot_checks "$OUT/$RNAME" robotics_board_rev011
  NAMES+=("$RNAME")
fi

cp "$BUILD/agent/rtos-agent" "$OUT/rtos-agent-$VERSION-aarch64"
( cd "$OUT" && sha256sum "${NAMES[@]}" "rtos-agent-$VERSION-aarch64" > SHA256SUMS )
for n in "${NAMES[@]}"; do check "ok: $OUT/$n"; done
cat "$OUT/SHA256SUMS"
say "done: ${NAMES[*]}"
