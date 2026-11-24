#!/usr/bin/env python3
"""Create verified GitHub release assets outside the source checkout."""
import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

REPO = Path(__file__).resolve().parents[1]


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def package(source, destination, version):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-.][A-Za-z0-9.-]+)?", version):
        raise ValueError("invalid release version")
    if destination.exists():
        raise FileExistsError(f"asset directory already exists: {destination}")
    if destination.resolve().is_relative_to(REPO):
        raise ValueError("release assets must be outside the source checkout")
    # 0.5.0+: one image per board variant; older builds: a single "-duos-" image.
    suffixes = ("duos-standalone-arm64-sd.img", "robotics-board-arm64-sd.img", "duos-arm64-sd.img")
    images = sorted(source / f"beitlab-robotics-milkv-duo-os-{version}-{suffix}" for suffix in suffixes
                    if (source / f"beitlab-robotics-milkv-duo-os-{version}-{suffix}").is_file())
    if not images:
        raise FileNotFoundError(f"no beitlab {version} image in {source}")
    agent = source / f"rtos-agent-{version}-aarch64"
    expected = {}
    for row in (source / "SHA256SUMS").read_text().splitlines():
        checksum, name = row.split(maxsplit=1)
        expected[name.lstrip("*")] = checksum
    for path in (*images, agent):
        if expected.get(path.name) != digest(path):
            raise ValueError(f"build checksum mismatch: {path.name}")
    destination.mkdir(parents=True)
    try:
        for image in images:
            compressed = destination / (image.name + ".xz")
            with compressed.open("xb") as stream:
                subprocess.run(["xz", "-T0", "-3", "-c", str(image)], stdout=stream, check=True)
            # A sidecar for the extracted image is useful to non-imager writers.
            (destination / (image.name + ".sha256")).write_text(f"{expected[image.name]}  {image.name}\n")
        shutil.copyfile(agent, destination / agent.name)
        tools = destination / f"beitlab-imager-tools-{version}.zip"
        with zipfile.ZipFile(tools, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted((REPO / "scripts").glob("*.py")):
                if path.name.startswith(("beitlab-imager", "imager_", "configure-")):
                    archive.write(path, "scripts/" + path.name)
            for name in ("FLASH.md", "SECURITY.md", "LICENSING.md"):
                archive.write(REPO / "docs" / name, "docs/" + name)
            archive.write(REPO / "imager.json", "imager.json")
            archive.write(REPO / "VERSION", "VERSION")
            archive.write(REPO / "THIRD_PARTY_NOTICES", "THIRD_PARTY_NOTICES")
            archive.writestr("START-HERE.txt",
                             "Install Python 3.10+, mtools and OpenSSL. See docs/FLASH.md.\n\n"
                             "Local image (current development workflow):\n"
                             "  python3 scripts/beitlab-imager.py --image /path/to/image.img.xz\n\n"
                             "Public GitHub Releases (once the configured repository is public and has a published image):\n"
                             "  python3 scripts/beitlab-imager.py\n"
                             "Downloads and verifies the latest published image before setup and flashing.\n"
                             "No GitHub account or GitHub CLI is needed for public downloads.\n"
                             "The release repository is set in imager.json.\n")
        notices = destination / f"beitlab-upstream-notices-{version}.zip"
        with zipfile.ZipFile(notices, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            root = REPO / "image/overlay/usr/share/licenses/beitlab"
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    archive.write(path, "licenses/" + str(path.relative_to(root)))
            archive.write(REPO / "THIRD_PARTY_NOTICES", "THIRD_PARTY_NOTICES")
            archive.write(REPO / "docs/LICENSING.md", "LICENSING.md")
        for name in ("build-checks.txt", "dtb-check.txt"):
            shutil.copyfile(source / name, destination / name)
        # Describe only the images this build produced (a standalone-only build has one).
        kinds = [label for suffix, label in (
            ("duos-standalone-arm64-sd.img", "duos-standalone for a Milk-V Duo S alone"),
            ("robotics-board-arm64-sd.img", "robotics-board for the Beitlab Robotics Board (and Balance Robot v1)"),
            ("duos-arm64-sd.img", "duos for a Milk-V Duo S"))
            if any(image.name.endswith("-" + suffix) for image in images)]
        imager = ("open it with beitlab Imager, which asks which board the card is for" if len(kinds) > 1
                  else "open it with beitlab Imager")
        (destination / "release-notes.md").write_text(
            f"beitlab Robotics MilkV duo OS {version}\n\n"
            f"Duo S ARM64 SD image{'s' if len(kinds) > 1 else ''}: {', '.join(kinds)}. "
            f"Extract the .img.xz or {imager}.\n"
            "Use the regular beitlab account and provision your SSH public key before boot.\n"
            "Root SSH is disabled; there is no default password.\n\n"
            "Includes bl_send; the beitlab group can use the C906 mailbox without sudo.\n"
            "General administration still requires password-authenticated sudo.\n\n"
            "Build, remoteproc/DT, account and Wi-Fi payload checks passed. "
            "Fresh-card hardware verification is still required.\n\n"
            "This draft includes selected upstream notices. Complete corresponding-source "
            "and vendor firmware permission review remains required before publication. "
            "See docs/LICENSING.md in the source repository.\n")
        rows = [f"{digest(path)}  {path.name}\n" for path in sorted(destination.iterdir())]
        (destination / "SHA256SUMS").write_text("".join(rows))
    except BaseException:
        shutil.rmtree(destination)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--asset-dir", type=Path, required=True)
    args = parser.parse_args()
    package(args.build_dir, args.asset_dir, (REPO / "VERSION").read_text().strip())
