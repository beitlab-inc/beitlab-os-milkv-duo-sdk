#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Write beitlab Robotics MilkV duo OS to a microSD card (Linux).
#
#   sudo scripts/flash.sh /dev/sdX [out/beitlab-robotics-milkv-duo-os-<version>-duos-standalone-arm64-sd.img]
#
# Checks the image against out/SHA256SUMS, shows the target with lsblk,
# refuses disks that hold mounted system filesystems, and asks you to type
# the device name a second time before writing. balenaEtcher or Raspberry
# Pi Imager ("Use custom") do the same job without this script.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
die() { echo "flash: $*" >&2; exit 1; }

DEV="${1:-}"
WORK="${BEITLAB_WORK_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/beitlab-duo-os}"
RELEASE="${BEITLAB_OUTPUT_DIR:-$WORK/releases/$(cat "$REPO/VERSION")}"
IMG="${2:-$RELEASE/beitlab-robotics-milkv-duo-os-$(cat "$REPO/VERSION")-duos-standalone-arm64-sd.img}"
[ -n "$DEV" ] || die "usage: sudo $0 /dev/sdX [image]"
[ -b "$DEV" ] || die "$DEV is not a block device"
[ -f "$IMG" ] || die "$IMG not found (run scripts/build-image.sh or download the release)"
[ "$(id -u)" = 0 ] || die "run with sudo"
case "$DEV" in *[0-9]) case "$DEV" in /dev/mmcblk*|/dev/nvme*|/dev/loop*) ;; *) die "$DEV looks like a partition; give the whole disk (e.g. /dev/sdb)";; esac;; esac

SUMS="$(dirname "$IMG")/SHA256SUMS"
if [ -f "$IMG.sha256" ]; then
  ( cd "$(dirname "$IMG")" && sha256sum -c "$(basename "$IMG").sha256" ) || die "checksum mismatch"
elif [ -f "$SUMS" ]; then
  ( cd "$(dirname "$IMG")" && grep " $(basename "$IMG")\$" SHA256SUMS | sha256sum -c - ) || die "checksum mismatch"
else
  echo "flash: no SHA256SUMS next to the image; skipping the checksum"
fi

for mp in / /boot /home /usr /var; do
  src="$(findmnt -n -o SOURCE "$mp" 2>/dev/null || true)"
  [ -n "$src" ] || continue
  parent="/dev/$(lsblk -n -o PKNAME "$src" 2>/dev/null | head -1)"
  [ "$parent" = "$DEV" ] || [ "$src" = "$DEV" ] && die "$DEV holds $mp of this computer; refusing"
done

echo
lsblk -o NAME,SIZE,MODEL,TRAN,MOUNTPOINTS "$DEV"
echo
echo "Everything on $DEV will be erased."
read -r -p "Type the device name again to confirm ($DEV): " again
[ "$again" = "$DEV" ] || die "names differ; nothing written"

for p in $(lsblk -n -l -o NAME "$DEV" | tail -n +2); do
  umount "/dev/$p" 2>/dev/null || true
done
dd if="$IMG" of="$DEV" bs=4M conv=fsync status=progress
sync
echo "flash: done. Remove the card, set the Duo S boot switch to ARM, insert it, power on."
