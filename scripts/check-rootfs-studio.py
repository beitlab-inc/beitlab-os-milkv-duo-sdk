#!/usr/bin/env python3
"""Check a variant's rootfs tarball for the Studio parts of OS 0.5.0 (PROMPT.md 0.5.0).

  check-rootfs-studio.py ROOTFS.tar.xz duos_standalone|robotics_board_rev011

Python 3 present (standard library and ssl, no pip), beitlab-link and its units enabled, the
board identity files, the inspector as the Robotics Board starter, and no Studio secrets.
"""

import re
import sys
import tarfile

WANTS = "etc/systemd/system/multi-user.target.wants/"
UNITS = ("beitlab-link", "beitlab-studio-import", "beitlab-board-status")
INSPECTOR_MARK = b"beitlab board inspector"


def check(path, hardware):
    with tarfile.open(path, "r:*") as archive:
        members = {m.name.removeprefix("./"): m for m in archive.getmembers()}

        def require(name):
            if name not in members:
                raise AssertionError(f"rootfs is missing /{name}")
            return members[name]

        def content(name):
            return archive.extractfile(require(name)).read()

        def executable(name):
            m = require(name)
            target = m
            if m.issym():
                link = m.linkname if m.linkname.startswith("/") else "/" + name.rsplit("/", 1)[0] + "/" + m.linkname
                target = require(link.lstrip("/").replace("/./", "/"))
            assert target.mode & 0o111, f"/{name} is not executable"

        executable("usr/bin/python3")
        libs = [n for n in members if re.fullmatch(r"usr/lib/python3\.\d+/ssl\.pyc?", n)]
        assert libs, "python3 has no ssl module (the link needs HTTPS)"
        assert [n for n in members if re.fullmatch(r"usr/lib/python3\.\d+/lib-dynload/_ssl\..*\.so", n)], \
            "python3 has no _ssl extension"
        pips = [n for n in members if re.fullmatch(r"usr/bin/pip3?(\.\d+)?", n)
                or re.fullmatch(r"usr/lib/python3\.\d+/site-packages/pip/__init__\.pyc?", n)]
        assert not pips, f"pip must not be on the board: {pips}"

        for name in ("usr/bin/beitlab-link", "usr/bin/beitlab-board-status", "usr/libexec/playground/import-studio"):
            executable(name)
        for unit in UNITS:
            require(f"usr/lib/systemd/system/{unit}.service")
            require(f"{WANTS}{unit}.service")
        link_unit = content("usr/lib/systemd/system/beitlab-link.service").decode()
        assert "User=beitlab" in link_unit and "LoadCredential=device-secret:" in link_unit

        assert content("etc/beitlab/hardware").decode().strip() == hardware, "wrong /etc/beitlab/hardware"
        assert content("boot/playground/board").decode().strip() == hardware, "wrong boot board marker"
        assert content("boot/playground/studio-supported").decode().strip() == "beitlab Studio link provisioning v1"
        os_release = content("usr/lib/os-release").decode()
        slug = {"duos_standalone": "duos-standalone", "robotics_board_rev011": "robotics-board"}[hardware]
        assert f"VARIANT_ID={slug}" in os_release, "os-release has the wrong VARIANT_ID"
        starter = content("usr/lib/firmware/playground-starter.elf")
        assert starter == content("usr/lib/firmware/playground.elf"), "playground.elf is not the starter"
        is_inspector = INSPECTOR_MARK in starter
        if hardware == "robotics_board_rev011":
            assert is_inspector, "the Robotics Board starter must be the board inspector"
        else:
            assert not is_inspector, "the standalone image must not ship the inspector as its starter"

        for secret in ("etc/beitlab/studio/device-secret", "etc/beitlab/studio/url", "boot/playground/device-secret",
                       "boot/playground/studio.conf"):
            assert secret not in members, f"release rootfs contains /{secret}"
    print(f"ok: {hardware}: python3 (ssl, no pip), beitlab-link enabled, board identity, starter, no Studio secrets")


if __name__ == "__main__":
    try:
        check(sys.argv[1], sys.argv[2])
    except (AssertionError, IndexError) as error:
        sys.exit(f"check-rootfs-studio: {error}")
