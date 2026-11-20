"""Check release checksums, compressed-image bytes and bundled CLI files."""
import hashlib
import importlib.util
import lzma
from pathlib import Path
import tempfile
import shutil
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location("release_package", Path(__file__).resolve().parents[1] / "package-release.py")
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


class PackageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "build"
        self.source.mkdir()
        self.destination = self.root / "assets"
        self.image = self.source / "beitlab-robotics-milkv-duo-os-1.2.3-duos-arm64-sd.img"
        self.agent = self.source / "rtos-agent-1.2.3-aarch64"
        self.image.write_bytes(b"fake image bytes\0" * 256)
        self.agent.write_bytes(b"agent binary fixture")
        self.source.joinpath("SHA256SUMS").write_text("".join(
            f"{RELEASE.digest(p)}  {p.name}\n" for p in (self.image, self.agent)))
        for name in ("build-checks.txt", "dtb-check.txt"):
            self.source.joinpath(name).write_text("checked\n")

    def test_round_trip_and_complete_checksums(self):
        original = self.image.read_bytes()
        RELEASE.package(self.source, self.destination, "1.2.3")
        self.assertEqual(self.image.read_bytes(), original)
        self.assertEqual(lzma.decompress((self.destination / (self.image.name + ".xz")).read_bytes()), original)
        manifest = {}
        for row in (self.destination / "SHA256SUMS").read_text().splitlines():
            digest, name = row.split(maxsplit=1)
            manifest[name] = digest
            self.assertEqual(digest, hashlib.sha256((self.destination / name).read_bytes()).hexdigest())
        self.assertEqual(set(manifest), {p.name for p in self.destination.iterdir()} - {"SHA256SUMS"})
        with zipfile.ZipFile(self.destination / "beitlab-imager-tools-1.2.3.zip") as archive:
            self.assertIsNone(archive.testzip())
            for name in ("beitlab-imager.py", "configure-wifi.py", "configure-image.py", "imager_sources.py", "imager_drives.py"):
                self.assertIn("scripts/" + name, archive.namelist())
            self.assertIn("imager.json", archive.namelist())

    @unittest.skipUnless(all(shutil.which(name) for name in ('mcopy', 'mdir', 'mmd', 'mkfs.vfat')), 'needs FAT tools')
    def test_extracted_bundle_prepares_a_real_image(self):
        from test_imager import RealFATWizardTest
        RELEASE.package(self.source, self.destination, "1.2.3")
        extracted = self.root / "extracted"
        with zipfile.ZipFile(self.destination / "beitlab-imager-tools-1.2.3.zip") as archive:
            archive.extractall(extracted)
        wizard = RealFATWizardTest('test_cli_prepares_image_over_a_real_terminal_without_touching_disks')
        wizard.script_directory = extracted / 'scripts'
        wizard.test_cli_prepares_image_over_a_real_terminal_without_touching_disks()

    def test_reject_corrupt_build_and_existing_destination(self):
        self.agent.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "checksum"):
            RELEASE.package(self.source, self.destination, "1.2.3")
        self.assertFalse(self.destination.exists())
        self.destination.mkdir()
        with self.assertRaises(FileExistsError):
            RELEASE.package(self.source, self.destination, "1.2.3")

    def test_reject_version_path_injection(self):
        with self.assertRaises(ValueError):
            RELEASE.package(self.source, self.destination, "../bad")


if __name__ == "__main__":
    unittest.main()
