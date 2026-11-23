"""OS 0.5.0 Studio settings and image variants (PROMPT.md "Studio link and hardware identity").

Real FAT and ext4 images built with mkfs/mtools, import-studio run under BusyBox, the derived
Robotics Board image checked with debugfs, and the rootfs check on synthetic tarballs.
"""
import hashlib
import importlib.util
import io
import os
import runpy
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONFIG = runpy.run_path(str(REPO / "scripts/configure-wifi.py"))
SPEC = importlib.util.spec_from_file_location("make_variant", REPO / "scripts/make-variant.py")
VARIANT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VARIANT)
SPEC2 = importlib.util.spec_from_file_location("check_studio", REPO / "scripts/check-rootfs-studio.py")
CHECK = importlib.util.module_from_spec(SPEC2)
SPEC2.loader.exec_module(CHECK)
SOURCES = runpy.run_path(str(REPO / "scripts/imager_sources.py"))
SECRET = "0123456789abcdef" * 4
HAVE_TOOLS = all(shutil.which(t) for t in ("mkfs.vfat", "mkfs.ext4", "mcopy", "mdir", "debugfs", "e2fsck"))
INFO = {"version": "0.5.0", "sdk_url": "https://example.invalid/sdk", "sdk_commit": "6f8962c394dd" + "0" * 28,
        "sdk_target": "milkv-duos-glibc-arm64-sd", "starter_desc": "test", "readme": "README"}


def make_image(path, fat_files, rootfs_dir):
    """MBR image: FAT 'boot' (type 0x0c) at 1 MiB, ext4 rootfs (type 0x83) after it."""
    with tempfile.TemporaryDirectory() as tmp:
        fat, ext = Path(tmp) / "boot.vfat", Path(tmp) / "root.ext4"
        subprocess.run(["mkfs.vfat", "-C", "-n", "boot", str(fat), str(16 * 1024)], check=True, capture_output=True)
        subprocess.run(["mmd", "-i", str(fat), "::/playground"], check=True, capture_output=True)
        for name, text in fat_files.items():
            src = Path(tmp) / "src"
            src.write_text(text)
            subprocess.run(["mcopy", "-i", str(fat), str(src), f"::/playground/{name}"], check=True, capture_output=True)
        subprocess.run(["mkfs.ext4", "-q", "-F", "-d", str(rootfs_dir), str(ext), "16M"], check=True, capture_output=True)
        fat_lba, fat_size = 2048, fat.stat().st_size
        ext_lba = fat_lba + fat_size // 512
        mbr = bytearray(512)
        for i, (ptype, lba, size) in enumerate(((0x0C, fat_lba, fat_size), (0x83, ext_lba, ext.stat().st_size))):
            mbr[446 + 16 * i + 4] = ptype
            struct.pack_into("<II", mbr, 446 + 16 * i + 8, lba, size // 512)
        mbr[510:] = b"\x55\xaa"
        with path.open("wb") as out:
            out.write(mbr)
            out.write(b"\0" * (fat_lba * 512 - 512))
            out.write(fat.read_bytes())
            out.write(ext.read_bytes())


class StudioSettings(unittest.TestCase):
    def test_claim_code_vectors_match_link_and_hub(self):
        for secret, code in (("00" * 32, "ATPS24"), (SECRET, "KPGFJP"), ("ff" * 32, "FW514V")):
            self.assertEqual(CONFIG["claim_code"](secret), code)

    def test_studio_url_rules(self):
        for url in ("https://studio.beitlab.com", "http://192.168.1.116:8090/", "http://beitlabgpu.local:8090"):
            self.assertEqual(CONFIG["studio_url"](url), url.rstrip("/"))
        for url in ("http://studio.beitlab.com", "ftp://x", "https://user@host", "https://h/?q=1",
                    "https://h/a b", "http://8.8.8.8", "javascript:alert(1)"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                CONFIG["studio_url"](url)

    def test_studio_files_and_card(self):
        files, code = CONFIG["studio_settings"]("https://studio.beitlab.com", SECRET)
        self.assertEqual(files, {"studio.conf": "https://studio.beitlab.com\n", "device-secret": SECRET + "\n"})
        self.assertEqual(code, "KPGFJP")
        fresh, other = CONFIG["studio_settings"]("https://studio.beitlab.com")
        self.assertRegex(fresh["device-secret"], r"^[0-9a-f]{64}\n$")
        self.assertNotEqual(fresh["device-secret"], files["device-secret"])
        card = CONFIG["board_card"]("robot-07", "duos_standalone", code, "https://studio.beitlab.com")
        self.assertIn("Claim code:  KPGFJP", card)
        self.assertIn("Milk-V Duo S (standalone image)", card)

    def test_studio_needs_a_hostname(self):
        files, _ = CONFIG["studio_settings"]("https://studio.beitlab.com", SECRET)
        with self.assertRaisesRegex(ValueError, "hostname"):
            CONFIG["provisioning_files"](studio=files)

    def test_mounted_card_checks_marker_and_board(self):
        files, _ = CONFIG["studio_settings"]("https://studio.beitlab.com", SECRET)
        with tempfile.TemporaryDirectory() as tmp:
            boot = Path(tmp)
            (boot / "playground").mkdir()
            (boot / "playground/user-supported").write_text("x")
            with self.assertRaisesRegex(ValueError, "OS 0.5.0"):
                CONFIG["configure_boot"](boot, hostname="robot-07", studio=files)
            (boot / "playground/studio-supported").write_text("x")
            (boot / "playground/board").write_text("robotics_board_rev011\n")
            with self.assertRaisesRegex(ValueError, "not the Milk-V Duo S"):
                CONFIG["configure_boot"](boot, hostname="robot-07", studio=files, board="duos_standalone")
            CONFIG["configure_boot"](boot, hostname="robot-07", studio=files, board="robotics_board_rev011")
            self.assertEqual((boot / "playground/device-secret").read_text(), SECRET + "\n")
            self.assertEqual(oct((boot / "playground/device-secret").stat().st_mode & 0o777), "0o600")


@unittest.skipUnless(HAVE_TOOLS, "needs mkfs.vfat, mkfs.ext4, mtools and e2fsprogs")
class RealImages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        rootfs = self.root / "rootfs"
        files = VARIANT.variant_files("standalone", INFO, b"\x7fELF standalone starter")
        VARIANT.write_overlay(rootfs, files)
        (rootfs / "etc/hostname").write_text("beitlab-milkv-duo\n")
        self.image = self.root / "base.img"
        make_image(self.image, {"wifi-supported": "w", "setup-supported": "s", "user-supported": "u",
                                "studio-supported": "beitlab Studio link provisioning v1\n",
                                "board": "duos_standalone\n"}, rootfs)

    def fat(self, image, name):
        off = CONFIG["boot_offset"](image)
        return subprocess.run(["mcopy", "-i", f"{image}@@{off}", f"::/playground/{name}", "-"],
                              capture_output=True, text=True).stdout

    def test_configure_image_writes_studio_files_and_checks_board(self):
        files, code = CONFIG["studio_settings"]("http://192.168.1.116:8090", SECRET)
        out = self.root / "personal.img"
        CONFIG["configure_image"](self.image, out, hostname="robot-07", studio=files, board="duos_standalone")
        self.assertEqual(self.fat(out, "studio.conf"), "http://192.168.1.116:8090\n")
        self.assertEqual(self.fat(out, "device-secret"), SECRET + "\n")
        self.assertEqual(self.fat(out, "hostname"), "robot-07\n")
        self.assertEqual(self.fat(self.image, "device-secret"), "")  # the release image stays clean
        with self.assertRaisesRegex(ValueError, "not the Beitlab Robotics Board"):
            CONFIG["configure_image"](self.image, self.root / "x.img", hostname="robot-07", studio=files,
                                      board="robotics_board_rev011")

    def test_derive_robotics_board_image(self):
        tarball = self.root / "rootfs.tar.xz"
        with tarfile.open(tarball, "w:xz") as tar:
            tar.add(self.root / "rootfs", arcname=".")
        out, out_tar = self.root / "robotics.img", self.root / "robotics.tar.xz"
        files = VARIANT.variant_files("robotics-board", INFO, b"\x7fELF beitlab board inspector")
        VARIANT.derive(self.image, out, files, "robotics_board_rev011", tarball, out_tar)
        self.assertEqual(self.fat(out, "board"), "robotics_board_rev011\n")
        self.assertEqual(self.fat(self.image, "board"), "duos_standalone\n")  # the base image is unchanged
        parts = VARIANT.partitions(out)
        root = next(p for p in parts if p[0] == 0x83)
        fs = self.root / "check.ext4"
        VARIANT.copy_range(out, fs, root[1], root[2])
        self.assertEqual(VARIANT.debugfs_cat(fs, "etc/beitlab/hardware"), b"robotics_board_rev011\n")
        self.assertEqual(VARIANT.debugfs_cat(fs, "usr/lib/firmware/playground-starter.elf"),
                         b"\x7fELF beitlab board inspector")
        self.assertIn(b"VARIANT_ID=robotics-board", VARIANT.debugfs_cat(fs, "usr/lib/os-release"))
        stat = subprocess.run(["debugfs", "-R", "stat /etc/beitlab/hardware", str(fs)], capture_output=True, text=True)
        self.assertRegex(stat.stdout, r"Mode:\s+0644")
        self.assertRegex(stat.stdout, r"User:\s+0\s+Group:\s+0")
        self.assertEqual(subprocess.run(["e2fsck", "-fn", str(fs)], capture_output=True).returncode, 0)
        with tarfile.open(out_tar) as tar:
            self.assertEqual(tar.extractfile("./etc/beitlab/hardware").read(), b"robotics_board_rev011\n")
        self.assertEqual(SOURCES["image_variant"](VARIANT.image_name("0.5.0", "robotics-board")),
                         "robotics_board_rev011")
        with self.assertRaises(FileExistsError):
            VARIANT.derive(self.image, out, files, "robotics_board_rev011")


@unittest.skipUnless(HAVE_TOOLS, "needs mkfs.vfat, mkfs.ext4, mtools and e2fsprogs")
class DeriveCreatesMissingDirectories(unittest.TestCase):
    def test_missing_directories_are_created_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "rootfs/etc").mkdir(parents=True)  # no etc/beitlab, no usr/lib/firmware
            (root / "rootfs/etc/hostname").write_text("x\n")
            image = root / "base.img"
            make_image(image, {"board": "duos_standalone\n"}, root / "rootfs")
            files = VARIANT.variant_files("robotics-board", INFO, b"\x7fELF beitlab board inspector")
            VARIANT.derive(image, root / "out.img", files, "robotics_board_rev011")  # runs e2fsck -fn
            part = next(p for p in VARIANT.partitions(root / "out.img") if p[0] == 0x83)
            fs = root / "fs.ext4"
            VARIANT.copy_range(root / "out.img", fs, part[1], part[2])
            self.assertEqual(VARIANT.debugfs_cat(fs, "usr/lib/firmware/playground.elf"),
                             b"\x7fELF beitlab board inspector")


@unittest.skipUnless(shutil.which("busybox"), "needs host BusyBox")
class ImportStudio(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "boot/playground").mkdir(parents=True)
        (self.root / "etc").mkdir()
        (self.root / "etc/issue").write_text("banner\nStudio claim code: OLD000\n")
        wrapper = self.root / "beitlab-link"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {REPO / 'link/beitlab-link.py'} \"$@\"\n")
        wrapper.chmod(0o755)
        script = (REPO / "image/overlay/usr/libexec/playground/import-studio").read_text()
        script = script.replace("BOOT=/boot/playground", f"BOOT={self.root}/boot/playground")
        script = script.replace("DIR=/etc/beitlab/studio", f"DIR={self.root}/etc/beitlab/studio")
        script = script.replace("ISSUE=/etc/issue", f"ISSUE={self.root}/etc/issue")
        script = script.replace("LINK=/usr/bin/beitlab-link", f"LINK={wrapper}")
        script = script.replace('chown 0:0 "$TEMP"', ":").replace("\nsync\n", "\n:\n")
        self.script = self.root / "import-studio"
        self.script.write_text(script)

    def run_import(self):
        return subprocess.run(["busybox", "sh", str(self.script)], capture_output=True, text=True)

    def test_valid_settings_imported_and_consumed(self):
        (self.root / "boot/playground/studio.conf").write_text("http://192.168.1.116:8090\r\n")
        (self.root / "boot/playground/device-secret").write_text(SECRET + "\n")
        r = self.run_import()
        self.assertEqual(r.returncode, 0, r.stderr)
        studio = self.root / "etc/beitlab/studio"
        self.assertEqual((studio / "url").read_text(), "http://192.168.1.116:8090\n")
        self.assertEqual((studio / "device-secret").read_text(), SECRET + "\n")
        self.assertEqual(oct((studio / "device-secret").stat().st_mode & 0o777), "0o600")
        self.assertEqual((studio / "claim-code").read_text(), "KPGFJP\n")
        issue = (self.root / "etc/issue").read_text()
        self.assertIn("Studio claim code: KPGFJP", issue)
        self.assertNotIn("OLD000", issue)
        self.assertEqual(list((self.root / "boot/playground").iterdir()), [])
        self.assertIn("KPGFJP", r.stdout)

    def test_bad_settings_ignored_and_consumed(self):
        (self.root / "boot/playground/studio.conf").write_text("http://x.example/$(reboot)\n")
        (self.root / "boot/playground/device-secret").write_text("not-a-secret\n")
        r = self.run_import()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("invalid Studio address", r.stderr)
        self.assertIn("invalid device secret", r.stderr)
        self.assertFalse((self.root / "etc/beitlab/studio/url").exists())
        self.assertFalse((self.root / "etc/beitlab/studio/device-secret").exists())
        self.assertEqual(list((self.root / "boot/playground").iterdir()), [])


class RootfsCheck(unittest.TestCase):
    def tarball(self, path, hardware="duos_standalone", *, starter=b"\x7fELF starter", extra=None, drop=()):
        variant = {"duos_standalone": "standalone", "robotics_board_rev011": "robotics-board"}[hardware]
        files = {rel: data for rel, data in VARIANT.variant_files(variant, INFO, starter).items()}
        files.update({
            "boot/playground/studio-supported": b"beitlab Studio link provisioning v1\n",
            "usr/bin/python3.12": b"elf", "usr/lib/python3.12/ssl.pyc": b"",
            "usr/lib/python3.12/lib-dynload/_ssl.cpython-312-aarch64-linux-gnu.so": b"",
            "usr/bin/beitlab-link": b"#!", "usr/bin/beitlab-board-status": b"#!",
            "usr/libexec/playground/import-studio": b"#!",
            "usr/lib/systemd/system/beitlab-link.service":
                b"[Service]\nUser=beitlab\nLoadCredential=device-secret:/etc/beitlab/studio/device-secret\n",
        })
        for unit in CHECK.UNITS:
            files.setdefault(f"usr/lib/systemd/system/{unit}.service", b"[Unit]\n")
            files[CHECK.WANTS + f"{unit}.service"] = b""
        files.update(extra or {})
        with tarfile.open(path, "w:xz") as tar:
            for rel, data in files.items():
                if rel in drop:
                    continue
                info = tarfile.TarInfo("./" + rel)
                info.size = len(data)
                info.mode = 0o755 if "/bin/" in rel or "libexec" in rel else 0o644
                tar.addfile(info, io.BytesIO(data))
            link = tarfile.TarInfo("./usr/bin/python3")
            link.type, link.linkname = tarfile.SYMTYPE, "python3.12"
            tar.addfile(link)
        return path

    def test_good_images_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            CHECK.check(self.tarball(Path(tmp) / "a.tar.xz"), "duos_standalone")
            CHECK.check(self.tarball(Path(tmp) / "b.tar.xz", "robotics_board_rev011",
                                     starter=b"\x7fELF beitlab board inspector"), "robotics_board_rev011")

    def test_failures(self):
        cases = [
            ("pip", {"extra": {"usr/bin/pip3": b"#!"}}, "pip"),
            ("no ssl", {"drop": ("usr/lib/python3.12/ssl.pyc",)}, "ssl"),
            ("secret", {"extra": {"etc/beitlab/studio/device-secret": b"x"}}, "device-secret"),
            ("not enabled", {"drop": (CHECK.WANTS + "beitlab-link.service",)}, "beitlab-link"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for name, kwargs, needle in cases:
                with self.subTest(name), self.assertRaisesRegex(AssertionError, needle):
                    CHECK.check(self.tarball(Path(tmp) / f"{name}.tar.xz", **kwargs), "duos_standalone")
            with self.assertRaisesRegex(AssertionError, "inspector"):
                CHECK.check(self.tarball(Path(tmp) / "r.tar.xz", "robotics_board_rev011"), "robotics_board_rev011")
            with self.assertRaisesRegex(AssertionError, "hardware"):
                CHECK.check(self.tarball(Path(tmp) / "w.tar.xz"), "robotics_board_rev011")


class Assets(unittest.TestCase):
    def test_variant_selection(self):
        names = ["beitlab-robotics-milkv-duo-os-0.5.0-duos-standalone-arm64-sd.img.xz",
                 "beitlab-robotics-milkv-duo-os-0.5.0-robotics-board-arm64-sd.img.xz",
                 "beitlab-robotics-milkv-duo-os-0.5.0-duos-standalone-arm64-sd.img.xz.sha256", "SHA256SUMS"]
        release = {"assets": [{"name": n} for n in names]}
        self.assertEqual(SOURCES["release_asset"](release, "robotics_board_rev011")["name"], names[1])
        self.assertEqual(SOURCES["release_asset"](release, "duos_standalone")["name"], names[0])
        self.assertIsNone(SOURCES["image_variant"]("beitlab-robotics-milkv-duo-os-0.4.1-duos-arm64-sd.img"))
        self.assertEqual(VARIANT.image_name("0.5.0", "standalone"), names[0].removesuffix(".xz"))
        digest = hashlib.sha256(b"").hexdigest()
        self.assertEqual(len(digest), 64)


if __name__ == "__main__":
    unittest.main()
