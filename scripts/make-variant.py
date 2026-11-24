#!/usr/bin/env python3
"""Board image variants (PROMPT.md 0.5.0, "Two images from one build").

The SDK build cleans and rebuilds everything, so it runs once, for the standalone variant. The
Robotics Board image is derived from that finished image by rewriting only the variant files:
in the ext4 root partition with debugfs and on the FAT boot volume with mtools. Nothing is
mounted and no root is needed. One function (variant_files) defines the files for both, so the
two images cannot drift apart.

  make-variant.py overlay --variant standalone --dir OVERLAY ...     before ./build.sh
  make-variant.py derive  --variant robotics-board --image IN.img --out OUT.img
                          --tarball IN.tar.xz --out-tarball OUT.tar.xz ...

Common options: --version --sdk-url --sdk-commit --sdk-target --starter ELF --starter-desc --readme.
"""

from __future__ import annotations

import argparse
import io
import lzma
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

VARIANTS = {
    "standalone": {"hardware": "duos_standalone", "profile": "duos_bare",
                   "line": "Milk-V Duo S standalone", "slug": "duos-standalone"},
    "robotics-board": {"hardware": "robotics_board_rev011", "profile": "beitlab_robotics_rev011",
                       "line": "Beitlab Robotics Board rev 0.11", "slug": "robotics-board"},
}
FAT_BOARD = "playground/board"


def image_name(version: str, variant: str) -> str:
    return f"beitlab-robotics-milkv-duo-os-{version}-{VARIANTS[variant]['slug']}-arm64-sd.img"


def variant_files(variant: str, info: dict, starter: bytes) -> dict[str, bytes]:
    """Root-filesystem files (paths without a leading /) that differ between the variants. All are
    root-owned, mode 0644."""
    v = VARIANTS[variant]
    version = info["version"]
    release_line = (f"beitlab Robotics MilkV duo OS {version} ({v['line']}) "
                    f"(sdk {info['sdk_commit'][:12]}, starter {info['starter_desc']})")
    return {
        "etc/beitlab/hardware": f"{v['hardware']}\n".encode(),
        "etc/beitlab/profile": f"{v['profile']}\n".encode(),
        "etc/duo-playground-release": (
            f"DUO_PLAYGROUND_VERSION={version}\nDUO_PLAYGROUND_VARIANT={v['hardware']}\n"
            f"SDK_URL={info['sdk_url']}\nSDK_COMMIT={info['sdk_commit']}\nSDK_TARGET={info['sdk_target']}\n"
            f"STARTER_FIRMWARE={info['starter_desc']}\nRELEASE_LINE=\"{release_line}\"\n").encode(),
        "etc/motd": f"{release_line}\nREADME: {info['readme']}\n".encode(),
        # agetty prints /etc/issue on the serial console before the login prompt.
        "etc/issue": (
            f"\n{release_line}\nNetwork name: beitlab-milkv-duo.local\n"
            "SSH login: beitlab (provision an SSH public key before boot)\n"
            "No default password. Configure a user password before flashing or on J22.\n"
            "Administration: sudo; root SSH login is disabled.\n\n").encode(),
        "usr/lib/os-release": (
            f'NAME="beitlab Robotics MilkV duo OS"\nID=beitlab-milkv-duo\nID_LIKE=buildroot\n'
            f'VERSION="{version}"\nVERSION_ID="{version}"\nPRETTY_NAME="beitlab Robotics MilkV duo OS {version}"\n'
            f'VARIANT="{v["line"]}"\nVARIANT_ID={v["slug"]}\n').encode(),
        "usr/lib/firmware/playground.elf": starter,
        "usr/lib/firmware/playground-starter.elf": starter,
        # genimage copies the overlay's /boot/playground onto the FAT boot volume.
        "boot/" + FAT_BOARD: f"{v['hardware']}\n".encode(),
    }


def write_overlay(directory: Path, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        path = directory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.chmod(path, 0o644)


# ---- deriving a finished image -----------------------------------------------------------------

def partitions(image: Path) -> list[tuple[int, int, int]]:
    """(type, byte offset, byte size) of the MBR primary partitions."""
    with image.open("rb") as f:
        mbr = f.read(512)
    if len(mbr) != 512 or mbr[510:] != b"\x55\xaa":
        raise ValueError(f"{image}: no DOS partition table")
    out = []
    for i in range(4):
        entry = mbr[446 + 16 * i: 462 + 16 * i]
        start, sectors = struct.unpack_from("<II", entry, 8)
        if entry[4] and sectors:
            out.append((entry[4], start * 512, sectors * 512))
    return out


def copy_range(src: Path, dst: Path, offset: int, size: int, *, into: bool = False) -> None:
    """Copy `size` bytes at `offset` of src to dst (or, with into=True, dst's bytes back into src)."""
    mode = "r+b" if into else "rb"
    with src.open(mode) as image, dst.open("rb" if into else "xb") as part:
        if into:
            image.seek(offset)
            shutil.copyfileobj(part, image, 4 * 1024 * 1024)
        else:
            image.seek(offset)
            left = size
            while left:
                chunk = image.read(min(left, 4 * 1024 * 1024))
                if not chunk:
                    raise ValueError("image ends inside the root partition")
                part.write(chunk)
                left -= len(chunk)


def debugfs(fs: Path, commands: list[str], write: bool = False) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".debugfs", delete=False) as f:
        f.write("\n".join(commands) + "\n")
        script = f.name
    try:
        argv = ["debugfs"] + (["-w"] if write else []) + ["-f", script, str(fs)]
        p = subprocess.run(argv, capture_output=True, text=True, check=False)
    finally:
        os.unlink(script)
    if p.returncode != 0:
        raise ValueError(f"debugfs failed: {p.stderr.strip()}")
    return p.stdout + p.stderr


def debugfs_cat(fs: Path, rel: str) -> bytes:
    p = subprocess.run(["debugfs", "-R", f"cat /{rel}", str(fs)], capture_output=True, check=False)
    return p.stdout


def exists(fs: Path, path: str) -> bool:
    p = subprocess.run(["debugfs", "-R", f"stat {path}", str(fs)], capture_output=True, text=True, check=False)
    return "Inode:" in p.stdout


def rewrite_rootfs(fs: Path, files: dict[str, bytes], work: Path) -> None:
    # debugfs "mkdir" on an existing directory leaks a half-made inode, so only missing
    # directories are created, parents first.
    dirs = sorted({"/" + "/".join(rel.split("/")[:n]) for rel in files for n in range(1, rel.count("/") + 1)})
    commands = [f"mkdir {d}" for d in dirs if not exists(fs, d)]
    for i, (rel, data) in enumerate(sorted(files.items())):
        src = work / f"f{i}"
        src.write_bytes(data)
        if exists(fs, "/" + rel):
            commands.append(f"rm /{rel}")
        commands += [f"write {src} /{rel}", f"sif /{rel} mode 0100644", f"sif /{rel} uid 0", f"sif /{rel} gid 0"]
    for d in dirs:
        commands += [f"sif {d} uid 0", f"sif {d} gid 0"]
    debugfs(fs, commands, write=True)
    for rel, data in files.items():
        if debugfs_cat(fs, rel) != data:
            raise ValueError(f"/{rel} was not written correctly")
    check = subprocess.run(["e2fsck", "-fn", str(fs)], capture_output=True, text=True, check=False)
    if check.returncode != 0:
        raise ValueError(f"e2fsck found problems after the rewrite:\n{check.stdout}")


def rewrite_tarball(src: Path, dst: Path, files: dict[str, bytes]) -> None:
    """The same change to the SDK's rootfs tarball, so the rootfs checks can run on the variant."""
    wanted = {"./" + rel: data for rel, data in files.items()}
    seen = set()
    with tarfile.open(src, "r:xz") as tin, lzma.open(dst, "xb", preset=1) as raw, \
            tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tout:
        for member in tin:
            name = member.name if member.name.startswith("./") else "./" + member.name
            if name in wanted and member.isfile():
                data = wanted[name]
                member.size = len(data)
                member.mode, member.uid, member.gid = 0o644, 0, 0
                tout.addfile(member, io.BytesIO(data))
                seen.add(name)
            else:
                tout.addfile(member, tin.extractfile(member) if member.isfile() else None)
        for name, data in wanted.items():
            if name not in seen:
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o644
                tout.addfile(info, io.BytesIO(data))


def derive(image: Path, out: Path, files: dict[str, bytes], hardware: str,
           tarball: Path | None = None, out_tarball: Path | None = None) -> None:
    if out.exists():
        raise FileExistsError(out)
    parts = partitions(image)
    fat = next((p for p in parts if p[0] in (0x0B, 0x0C)), None)
    root = next((p for p in parts if p[0] == 0x83), None)
    if fat is None or root is None:
        raise ValueError(f"{image}: expected a FAT boot partition and a Linux root partition")
    rootfs_files = {k: v for k, v in files.items() if not k.startswith("boot/")}
    shutil.copyfile(image, out)
    try:
        with tempfile.TemporaryDirectory(prefix="beitlab-variant-") as tmp:
            work = Path(tmp)
            fs = work / "rootfs.ext4"
            copy_range(out, fs, root[1], root[2])
            rewrite_rootfs(fs, rootfs_files, work)
            copy_range(out, fs, root[1], root[2], into=True)
            board = work / "board"
            board.write_text(hardware + "\n")
            drive = f"{out}@@{fat[1]}"
            subprocess.run(["mcopy", "-o", "-i", drive, str(board), "::/" + FAT_BOARD], check=True,
                           capture_output=True)
            got = subprocess.run(["mcopy", "-i", drive, "::/" + FAT_BOARD, "-"], check=True,
                                 capture_output=True, text=True).stdout.strip()
            if got != hardware:
                raise ValueError("boot volume board marker was not written")
        if tarball is not None and out_tarball is not None:
            rewrite_tarball(tarball, out_tarball, files)
    except BaseException:
        out.unlink(missing_ok=True)
        if out_tarball is not None:
            out_tarball.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=["overlay", "derive", "name"])
    ap.add_argument("--variant", choices=sorted(VARIANTS), required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--sdk-url", default="")
    ap.add_argument("--sdk-commit", default="")
    ap.add_argument("--sdk-target", default="")
    ap.add_argument("--starter", type=Path)
    ap.add_argument("--starter-desc", default="")
    ap.add_argument("--readme", default="")
    ap.add_argument("--dir", type=Path)
    ap.add_argument("--image", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--tarball", type=Path)
    ap.add_argument("--out-tarball", type=Path)
    a = ap.parse_args(argv)
    if a.mode == "name":
        print(image_name(a.version, a.variant))
        return 0
    if a.starter is None:
        ap.error("--starter is required")
    info = {"version": a.version, "sdk_url": a.sdk_url, "sdk_commit": a.sdk_commit, "sdk_target": a.sdk_target,
            "starter_desc": a.starter_desc, "readme": a.readme}
    files = variant_files(a.variant, info, a.starter.read_bytes())
    try:
        if a.mode == "overlay":
            if a.dir is None:
                ap.error("overlay needs --dir")
            write_overlay(a.dir, files)
        else:
            if a.image is None or a.out is None:
                ap.error("derive needs --image and --out")
            derive(a.image, a.out, files, VARIANTS[a.variant]["hardware"], a.tarball, a.out_tarball)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"make-variant: {exc}", file=sys.stderr)
        return 1
    print(f"make-variant: {a.mode} {a.variant} done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
