#!/usr/bin/env python3
"""Check PC provisioning, real FAT image writes and BusyBox boot import."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import base64

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("configure_wifi", REPO / "scripts/configure-wifi.py")
WIFI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WIFI)
KEY_DATA = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes(range(32))
PUBLIC_KEY = "ssh-ed25519 " + base64.b64encode(KEY_DATA).decode() + " lab@pc\n"


class ProfileTest(unittest.TestCase):
    def test_hostname_validation(self):
        self.assertEqual(WIFI.hostname_config("Robot-01"), "robot-01\n")
        self.assertEqual(WIFI.hostname_config("a"), "a\n")
        for name in ("", "robot.local", "-robot", "robot-", "robot_01", "a" * 64,
                     "robot\n", "$(touch /tmp/x)"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                WIFI.hostname_config(name)

    def test_public_key_and_private_key_rejection(self):
        with tempfile.TemporaryDirectory() as temporary:
            key = Path(temporary) / 'id.pub'
            key.write_text('# public key\n' + PUBLIC_KEY + PUBLIC_KEY)
            self.assertEqual(WIFI.public_keys([key]), PUBLIC_KEY)
            for data in ('-----BEGIN OPENSSH PRIVATE KEY-----\n', 'ssh-ed25519 not-base64\n',
                         'ssh-rsa ' + base64.b64encode(KEY_DATA).decode(),
                         'ssh-ed25519 ' + base64.b64encode(KEY_DATA[:-1]).decode(), ''):
                key.write_text(data)
                with self.subTest(data=data), self.assertRaises(ValueError):
                    WIFI.public_keys([key])

    def test_psk_matches_wpa_reference_vector(self):
        result = WIFI.profile("IEEE", "password", "co")
        self.assertIn("psk=f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e", result)
        self.assertIn("country=CO\n", result)
        self.assertNotIn("password", result)

    def test_ssid_special_characters_are_encoded(self):
        ssid = 'Lab " \\ $ café'
        result = WIFI.profile(ssid, "secret123")
        self.assertIn(f"ssid={ssid.encode().hex()}\n", result)
        self.assertNotIn(ssid, result)

    def test_invalid_credentials(self):
        for ssid, password, country in (("", "secret123", None),
                                        ("é" * 17, "secret123", None),
                                        ("Lab", "short", None),
                                        ("Lab", "x" * 64, None),
                                        ("Lab\n", "secret123", None),
                                        ("Lab", "secret123\n", None),
                                        ("Lab", "secret123", "USA")):
            with self.subTest(ssid=ssid, country=country), self.assertRaises(ValueError):
                WIFI.profile(ssid, password, country)


@unittest.skipUnless(shutil.which("busybox"), "needs BusyBox")
class ImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "boot/playground").mkdir(parents=True)
        (self.root / "config").mkdir()
        self.source = self.root / "boot/playground/wifi.conf"
        self.saved = self.root / "config/beitlab-wlan0.conf"
        self.saved.write_text("old working profile\n")
        script = (REPO / "image/overlay/usr/libexec/playground/import-wifi").read_text()
        script = script.replace("/boot/playground", str(self.root / "boot/playground"))
        script = script.replace("/etc/wpa_supplicant", str(self.root / "config"))
        # Tests run without root and never need to sync the host disk.
        script = script.replace('chown 0:0 "$TEMP"', ':')
        script = script.replace('\nsync\n', '\n:\n')
        self.script = self.root / "import-wifi"
        self.script.write_text(script)

    def run_import(self):
        return subprocess.run(["busybox", "sh", str(self.script)], capture_output=True, text=True)

    def test_first_boot_import_and_subsequent_boot(self):
        config = WIFI.profile('Lab " \\ $ café', "secret123", "CO")
        self.source.write_bytes(config.replace("\n", "\r\n").encode())
        result = self.run_import()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("secret123", result.stdout + result.stderr)
        self.assertEqual(self.saved.read_text(), config)
        self.assertEqual(self.saved.stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.source.exists())
        self.assertEqual(self.run_import().returncode, 0)
        self.assertEqual(self.saved.read_text(), config)
        self.assertEqual(list(self.saved.parent.glob('.beitlab-*')), [])

    def test_import_without_country(self):
        config = WIFI.profile("Lab", "secret123")
        self.source.write_text(config)
        self.assertEqual(self.run_import().returncode, 0)
        self.assertEqual(self.saved.read_text(), config)

    def test_invalid_profiles_keep_saved_network_and_boot_file(self):
        config = WIFI.profile("Lab", "secret123")
        for invalid in (config + "include=/tmp/other.conf\n",
                        config.replace("update_config=0", "update_config=1"),
                        config.replace("ctrl_interface=/run/wpa_supplicant\n", ""),
                        config.replace("    scan_ssid=1\n", ""),
                        config.replace("    scan_ssid=1\n", "    scan_ssid=1\n    scan_ssid=1\n"),
                        config.replace("ssid=4c6162", "ssid=4c6"),
                        config.replace("psk=", "psk=invalid")):
            with self.subTest(invalid=invalid):
                self.source.write_text(invalid)
                self.assertNotEqual(self.run_import().returncode, 0)
                self.assertEqual(self.saved.read_text(), "old working profile\n")
                self.assertTrue(self.source.exists())
                self.assertEqual(list(self.saved.parent.glob('.beitlab-*')), [])


@unittest.skipUnless(all(shutil.which(t) for t in ("mkfs.vfat", "mcopy", "mmd", "mdir", "mdel")),
                     "needs dosfstools and mtools")
class ImageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / "release.img"
        fat = self.root / "boot.vfat"
        with fat.open("wb") as f:
            f.truncate(64 * 1024 * 1024)
        subprocess.run(["mkfs.vfat", "-n", "boot", str(fat)], check=True, capture_output=True)
        mbr = bytearray(512)
        mbr[450] = 0x0C
        struct.pack_into("<II", mbr, 454, 2048, fat.stat().st_size // 512)
        mbr[510:] = b"\x55\xaa"
        with self.image.open("wb") as f, fat.open("rb") as data:
            f.write(mbr)
            f.seek(2048 * 512)
            shutil.copyfileobj(data, f)
            f.write(b"root filesystem sentinel")
        self.drive = f"{self.image}@@{2048 * 512}"
        subprocess.run(["mmd", "-i", self.drive, "::/playground"], check=True)
        marker = self.root / "wifi-supported"
        marker.write_text("beitlab boot Wi-Fi provisioning v1\n")
        subprocess.run(["mcopy", "-i", self.drive, str(marker), "::/playground/wifi-supported"], check=True)
        marker = self.root / "user-supported"
        marker.write_text("beitlab regular-user provisioning v1\n")
        subprocess.run(["mcopy", "-i", self.drive, str(marker), "::/playground/user-supported"], check=True)
        marker = self.root / "setup-supported"
        marker.write_text("beitlab first-boot Wi-Fi, hostname and SSH public-key provisioning v1\n")
        subprocess.run(["mcopy", "-i", self.drive, str(marker), "::/playground/setup-supported"], check=True)

    def digest(self):
        return hashlib.sha256(self.image.read_bytes()).hexdigest()

    def test_personal_image_keeps_original_and_root_partition(self):
        before = self.digest()
        destination = self.root / "personal.img"
        config = WIFI.profile("Lab", "secret123")
        WIFI.configure_image(self.image, destination, config)
        self.assertEqual(self.digest(), before)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        checksum = destination.with_name(destination.name + ".sha256")
        self.assertEqual(checksum.read_text(),
                         hashlib.sha256(destination.read_bytes()).hexdigest() + "  personal.img\n")
        result = subprocess.run(["mcopy", "-i", f"{destination}@@{2048 * 512}",
                                 "::/playground/wifi.conf", "-"], check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout, config)
        with destination.open("rb") as f:
            f.seek(-24, os.SEEK_END)
            self.assertEqual(f.read(), b"root filesystem sentinel")
        with self.assertRaises(FileExistsError):
            WIFI.configure_image(self.image, destination, config)
        self.assertTrue(destination.exists())

    def test_old_image_without_importer_is_rejected(self):
        subprocess.run(["mdel", "-i", self.drive, "::/playground/wifi-supported"], check=True)
        destination = self.root / "personal.img"
        with self.assertRaisesRegex(ValueError, "0.2.0"):
            WIFI.configure_image(self.image, destination, WIFI.profile("Lab", "secret123"))
        self.assertFalse(destination.exists())

    def test_all_settings_in_image_and_original_is_kept(self):
        before = self.digest()
        destination = self.root / 'robot-01.img'
        config = WIFI.profile('Lab', 'secret123', 'CO')
        WIFI.configure_image(self.image, destination, config, 'Robot-01', PUBLIC_KEY)
        for name, expected in (('wifi.conf', config), ('hostname', 'robot-01\n'),
                               ('authorized_keys', PUBLIC_KEY)):
            result = subprocess.run(['mcopy', '-i', f'{destination}@@{2048*512}',
                                     f'::/playground/{name}', '-'], check=True, capture_output=True, text=True)
            self.assertEqual(result.stdout, expected)
        self.assertEqual(self.digest(), before)
        with destination.open('rb') as image:
            image.seek(-24, os.SEEK_END)
            self.assertEqual(image.read(), b'root filesystem sentinel')

    def test_old_wifi_only_image_rejects_hostname(self):
        subprocess.run(['mdel', '-i', self.drive, '::/playground/setup-supported'], check=True)
        with self.assertRaisesRegex(ValueError, '0.3.0'):
            WIFI.configure_image(self.image, self.root / 'robot.img', hostname='robot-01')
        self.assertFalse((self.root / 'robot.img').exists())

    def test_account_settings_require_new_marker_and_store_only_a_hash(self):
        user = WIFI.user_profile('a unique test password', True)
        destination = self.root / 'account.img'
        WIFI.configure_image(self.image, destination, ssh_keys=PUBLIC_KEY, user=user)
        data = subprocess.run(['mcopy', '-i', f'{destination}@@{2048*512}',
                               '::/playground/user.conf', '-'], check=True, capture_output=True, text=True).stdout
        self.assertEqual(data, user)
        self.assertNotIn('a unique test password', data)
        subprocess.run(['mdel', '-i', self.drive, '::/playground/user-supported'], check=True)
        with self.assertRaisesRegex(ValueError, '0.4.0'):
            WIFI.configure_image(self.image, self.root / 'old.img', ssh_keys=PUBLIC_KEY)


class MountedCardTest(unittest.TestCase):
    def test_hostname_and_key_without_wifi(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'playground').mkdir()
            (root / 'playground/setup-supported').touch()
            (root / 'playground/user-supported').touch()
            WIFI.configure_boot(root, hostname='robot-01', ssh_keys=PUBLIC_KEY)
            self.assertEqual((root / 'playground/hostname').read_text(), 'robot-01\n')
            self.assertEqual((root / 'playground/authorized_keys').read_text(), PUBLIC_KEY)
            self.assertFalse((root / 'playground/wifi.conf').exists())
            with self.assertRaises(FileExistsError):
                WIFI.configure_boot(root, hostname='another-name')

    def test_preflight_conflict_keeps_card_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'playground').mkdir()
            (root / 'playground/setup-supported').touch()
            (root / 'playground/user-supported').touch()
            (root / 'playground/authorized_keys').write_text(PUBLIC_KEY)
            with self.assertRaises(FileExistsError):
                WIFI.configure_boot(root, WIFI.profile('Lab', 'secret123'), 'robot-01', PUBLIC_KEY)
            self.assertFalse((root / 'playground/hostname').exists())
            self.assertFalse((root / 'playground/wifi.conf').exists())


@unittest.skipUnless(shutil.which('busybox'), 'needs BusyBox')
class HostnameAndKeyImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ('boot/playground', 'etc', 'root'):
            (self.root / directory).mkdir(parents=True)
        (self.root / 'etc/hostname').write_text('beitlab-milkv-duo\n')
        (self.root / 'etc/hosts').write_text('127.0.0.1 localhost\n127.0.1.1 beitlab-milkv-duo\n')
        (self.root / 'etc/issue').write_text('OS\nNetwork name: beitlab-milkv-duo.local\n')
        script = (REPO / 'image/overlay/usr/libexec/playground/import-hostname').read_text()
        script = script.replace('/boot/playground', str(self.root / 'boot/playground'))
        script = script.replace('/etc/', str(self.root / 'etc') + '/')
        script = script.replace('chown 0:0 "$TEMP"', ':')
        script = script.replace('hostname "$NAME"', f'printf "%s\\n" "$NAME" > "{self.root}/runtime-hostname"')
        script = script.replace('\nsync\n', '\n:\n')
        self.script = self.root / 'import-hostname'
        self.script.write_text(script)

    def run_import(self):
        return subprocess.run(['busybox', 'sh', str(self.script)], capture_output=True, text=True)

    def test_hostname_applies_runtime_persists_and_is_consumed(self):
        source = self.root / 'boot/playground/hostname'
        source.write_bytes(b'robot-01\r\n')
        result = self.run_import()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'runtime-hostname').read_text(), 'robot-01\n')
        self.assertEqual((self.root / 'etc/hostname').read_text(), 'robot-01\n')
        self.assertIn('127.0.1.1 robot-01', (self.root / 'etc/hosts').read_text())
        self.assertIn('127.0.0.1 localhost', (self.root / 'etc/hosts').read_text())
        self.assertIn('Network name: robot-01.local', (self.root / 'etc/issue').read_text())
        self.assertFalse(source.exists())
        self.assertEqual(self.run_import().returncode, 0)

    def test_invalid_hostname_keeps_current_name(self):
        source = self.root / 'boot/playground/hostname'
        for invalid in ('-bad', 'bad.local', 'bad\nname', 'name-', 'x' * 64, '$(echo bad)'):
            source.write_text(invalid)
            with self.subTest(name=invalid):
                self.assertNotEqual(self.run_import().returncode, 0)
                self.assertEqual((self.root / 'etc/hostname').read_text(), 'beitlab-milkv-duo\n')
                self.assertTrue(source.exists())

    def test_ssh_key_import_is_idempotent_and_handles_missing_newline(self):
        source = self.root / 'boot/playground/authorized_keys'
        source.write_bytes(PUBLIC_KEY.strip().encode() + b'\r')
        script = (REPO / 'image/overlay/usr/libexec/playground/install-keys').read_text()
        script = script.replace('/boot/playground', str(self.root / 'boot/playground'))
        script = script.replace('/home/beitlab/.ssh', str(self.root / 'root/.ssh'))
        script = script.replace('chown -R beitlab:beitlab', 'true')
        script = script.replace('\nsync\n', '\n:\n')
        path = self.root / 'install-keys'
        path.write_text(script)
        for _ in range(2):
            result = subprocess.run(['busybox', 'sh', str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        keys = self.root / 'root/.ssh/authorized_keys'
        self.assertEqual(keys.read_text(), PUBLIC_KEY)
        self.assertEqual(keys.stat().st_mode & 0o777, 0o600)
        self.assertEqual(keys.parent.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
