"""Safety, downloads and real-FAT CLI tests. Never flash a real device."""
import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import pty
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import imager_drives as drives
import imager_sources as sources

SPEC = importlib.util.spec_from_file_location("imager_cli", SCRIPTS / "beitlab-imager.py")
CLI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLI)


def disk(**changes):
    value = dict(name="sdc", path="/dev/sdc", type="disk", size=32_000_000_000,
                 model="USB card reader", serial="card-001", tran="usb", rm=True,
                 ro=False, diskseq="123", **{"maj:min": "8:32"}, children=[])
    value.update(changes)
    return value


class DriveSafetyTest(unittest.TestCase):
    def test_internal_system_readonly_empty_and_mapped_drives_are_blocked(self):
        examples = [disk(tran="nvme", rm=False), disk(ro=True), disk(size=0),
                    disk(children=[dict(type="part", path="/dev/sdc1", **{"maj:min": "8:33"}, mountpoints=["/media/card"])]),
                    disk(children=[dict(type="crypt", path="/dev/mapper/crypt", mountpoints=["/media/card"])]),
                    disk(mountpoints=["[SWAP]"]), disk(mountpoints=["/home/other-user"])]
        for item in examples:
            with self.subTest(item=item):
                self.assertFalse(drives.linux_drives({"blockdevices": [item]}, {"8:33"})[0].eligible)

    def test_removable_sd_and_usb_are_available_and_unmount_children_first(self):
        value = disk(children=[dict(type="part", path="/dev/sdc1", mountpoints=["/media/card"]),
                               dict(type="part", path="/dev/sdc2", mountpoints=[None])])
        found = drives.linux_drives({"blockdevices": [value]}, set())[0]
        self.assertTrue(found.eligible)
        self.assertEqual(found.unmount, ["/dev/sdc1"])
        self.assertEqual(found.sequence, "123")
        value = disk(tran=None, rm=True)
        self.assertTrue(drives.linux_drives({"blockdevices": [value]}, set())[0].eligible)

    def test_source_files_on_a_drive_protect_it(self):
        item = disk(children=[dict(type="part", path="/dev/sdc1", **{"maj:min": "8:33"}, mountpoints=["/mnt/images"])])
        self.assertIn("source", drives.linux_drives({"blockdevices": [item]}, {"8:33"})[0].reason)

    def test_hotplug_sequence_changes_identity_even_without_a_serial(self):
        before, after = [drives.linux_drives({"blockdevices": [disk(serial=None, diskseq=n)]}, set())[0]
                         for n in ("123", "124")]
        self.assertNotEqual(before.identity, after.identity)

    def test_changed_removed_or_undersized_drive_is_rejected(self):
        with tempfile.NamedTemporaryFile() as image:
            image.write(bytes(1024)); image.flush()
            for found in ([], [drives.Drive('/dev/sdc', 4096, 'card', 'other')],
                          [drives.Drive('/dev/sdc', 512, 'card', 'expected')],
                          [drives.Drive('/dev/sdc', 4096, 'card', 'expected', reason='system')]):
                with self.subTest(found=found), patch.object(drives, 'scan', return_value=found), self.assertRaises(ValueError):
                    drives.selected_drive('/dev/sdc', 'expected', Path(image.name))

    def test_cancel_confirmation_never_unmounts_or_opens_a_target(self):
        with tempfile.NamedTemporaryFile() as image:
            image.write(b'img'); image.flush()
            ui = CLI.UI(False, False)
            with patch.object(drives.sys.stdin, 'isatty', return_value=True), patch.object(drives.os, 'geteuid', return_value=0), \
                 patch.object(drives, 'selected_drive', return_value=drives.Drive('/dev/sdc', 9999, 'card', 'id')), \
                 patch.object(ui, 'ask', side_effect=['yes', 'q']), patch.object(drives, 'command') as command, \
                 patch.object(drives.os, 'open') as raw_open, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    drives.flash(Path(image.name), '/dev/sdc', 'id', hashlib.sha256(b'img').hexdigest(), ui)
                command.assert_not_called()
                raw_open.assert_not_called()

    def test_partial_confirmation_retries_without_unmounting(self):
        with tempfile.NamedTemporaryFile() as image:
            image.write(b'img'); image.flush()
            drive = drives.Drive('/dev/sdc', 9999, 'card', 'id')
            ui = CLI.UI(False, False)
            output = io.StringIO()
            with patch.object(drives.sys.stdin, 'isatty', return_value=True), patch.object(drives.os, 'geteuid', return_value=0), \
                 patch.object(drives, 'selected_drive', side_effect=[drive, ValueError('stopped after confirmation')]) as selected, \
                 patch.object(ui, 'ask', side_effect=['ERASE', 'ERASE /dev/sdc']) as ask, \
                 patch.object(drives, 'command') as command, patch.object(drives.os, 'open') as raw_open, \
                 contextlib.redirect_stdout(output):
                with self.assertRaisesRegex(ValueError, 'stopped after confirmation'):
                    drives.flash(Path(image.name), '/dev/sdc', 'id', hashlib.sha256(b'img').hexdigest(), ui)
                self.assertEqual(ask.call_count, 2)
                self.assertEqual(selected.call_count, 2)
                command.assert_not_called()
                raw_open.assert_not_called()
            self.assertIn('Enter the full phrase: ERASE /dev/sdc', output.getvalue())
            self.assertIn('No data has been written', output.getvalue())

    def test_modified_image_never_reaches_disk_selection(self):
        with tempfile.NamedTemporaryFile() as image:
            image.write(b'changed'); image.flush()
            with patch.object(drives.sys.stdin, 'isatty', return_value=True), patch.object(drives.os, 'geteuid', return_value=0), \
                 patch.object(drives, 'selected_drive') as selected, self.assertRaisesRegex(ValueError, 'changed'):
                drives.flash(Path(image.name), '/dev/sdc', 'id', '0' * 64, CLI.UI(False, False))
            selected.assert_not_called()

    def test_noninteractive_flash_is_refused(self):
        with patch.object(drives.sys.stdin, 'isatty', return_value=False), self.assertRaisesRegex(ValueError, 'interactive'):
            drives.flash(Path('/does/not/exist'), '/dev/sdc', 'id', '0' * 64, CLI.UI())

    def test_drive_changed_after_unmount_never_opens_a_target(self):
        with tempfile.NamedTemporaryFile() as image:
            image.write(b'img'); image.flush()
            drive = drives.Drive('/dev/sdc', 9999, 'card', 'id', unmount=['/dev/sdc1'])
            ui = CLI.UI(False, False)
            with patch.object(drives.sys.stdin, 'isatty', return_value=True), patch.object(drives.os, 'geteuid', return_value=0), \
                 patch.object(drives, 'selected_drive', side_effect=[drive, drive, ValueError('changed')]), \
                 patch.object(ui, 'ask', return_value='ERASE /dev/sdc'), patch.object(drives, 'command') as command, \
                 patch.object(drives.os, 'open') as raw_open, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError, 'changed'):
                    drives.flash(Path(image.name), '/dev/sdc', 'id', hashlib.sha256(b'img').hexdigest(), ui)
                command.assert_called_once_with(['umount', '--', '/dev/sdc1'])
                raw_open.assert_not_called()

    def test_macos_apfs_system_disk_is_resolved_to_physical_store(self):
        containers = {'disk3': {'PhysicalStores': [{'DeviceIdentifier': 'disk0s2'}]}}
        self.assertEqual(drives.mac_physical('/dev/disk3s1s1', containers), {'disk0'})
        listing = {'AllDisksAndPartitions': [{'DeviceIdentifier': 'disk0'}, {'DeviceIdentifier': 'disk2'}]}
        info = lambda name: {'DeviceIdentifier': name, 'TotalSize': 32_000_000_000,
                             'Internal': False, 'Whole': True, 'Writable': True, 'MediaName': 'card'}

        def command(argv):
            if argv[:3] == ['diskutil', 'list', '-plist']:
                return plistlib.dumps(listing)
            if argv[:3] == ['diskutil', 'apfs', 'list']:
                return plistlib.dumps({'Containers': [{'ContainerReference': 'disk3', **containers['disk3']}]})
            if argv[0] == 'df':
                return b'Filesystem 512-blocks Used Available Capacity Mounted on\n/dev/disk3s1s1 100 50 50 50% /\n'
            return plistlib.dumps(info(argv[-1]))
        with patch.object(drives, 'command', side_effect=command):
            found = drives.scan_macos([])
        self.assertFalse(found[0].eligible)
        self.assertTrue(found[1].eligible)


class CopyTest(unittest.TestCase):
    def test_write_and_readback_preserve_the_rest_of_a_device(self):
        payload = b'duo-image' * 1024
        with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as target:
            source.write(payload)
            target.write(bytes(len(payload)) + b'untouched tail')
            target.seek(0)
            stages = []
            drives.copy_and_verify(source, target, len(payload), hashlib.sha256(payload).hexdigest(),
                                   lambda label, *args: stages.append(label))
            self.assertEqual(target.read(), b'untouched tail')
            self.assertIn('Writing', stages)
            self.assertIn('Verifying', stages)

    def test_short_writes_are_completed(self):
        with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as underlying:
            source.write(b'1234567890')

            class ShortWriter:
                def write(self, value):
                    return underlying.write(value[:3])
                def __getattr__(self, name):
                    return getattr(underlying, name)
            drives.copy_and_verify(source, ShortWriter(), 10, hashlib.sha256(b'1234567890').hexdigest(), lambda *args: None)

    def test_failed_readback_and_truncated_source_are_errors(self):
        with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as target:
            source.write(b'1234')
            with self.assertRaisesRegex(ValueError, 'read-back'):
                drives.copy_and_verify(source, target, 4, '0' * 64, lambda *args: None)
            source.seek(0)
            with self.assertRaisesRegex(ValueError, 'unexpectedly'):
                drives.copy_and_verify(source, target, 100, '0' * 64, lambda *args: None)


class SourceTest(unittest.TestCase):
    def test_local_checksum_matches_exact_filename(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / 'local.img'
            image.write_bytes(b'image')
            sums = image.parent / 'SHA256SUMS'
            digest = hashlib.sha256(b'image').hexdigest()
            sums.write_text('0' * 64 + '  other.img\n' + digest + '  local.img\n')
            self.assertEqual(sources.adjacent_checksum(image), digest)
            self.assertEqual(sources.digest_file(image), digest)
            sums.write_text('0' * 64 + '  other.img\n')
            self.assertIsNone(sources.adjacent_checksum(image))

    def test_repository_configuration_and_override(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True), patch.object(sources.shutil, 'which', return_value=None):
            root = Path(folder)
            self.assertIsNone(sources.resolve_repository(None, root))
            (root / 'imager.json').write_text('{"github_repository": "beitlab/duo-os"}')
            self.assertEqual(sources.resolve_repository(None, root), 'beitlab/duo-os')
            self.assertEqual(sources.resolve_repository('owner/override', root), 'owner/override')
            with self.assertRaises(ValueError):
                sources.resolve_repository('https://example.com/repo', root)

    def test_github_download_verified_cached_and_checksum_fail_cleans_partial(self):
        payload = b'fake image contents'
        digest = hashlib.sha256(payload).hexdigest()
        name = 'beitlab-robotics-milkv-duo-os-0.4.0-duos-arm64-sd.img'
        release = {'assets': [{'name': name, 'browser_download_url': 'https://github.com/o/r/image', 'digest': 'sha256:' + digest}]}

        class Response(io.BytesIO):
            headers = {'Content-Length': str(len(payload))}

        with tempfile.TemporaryDirectory() as temporary, patch.object(sources, 'read_metadata', return_value=json.dumps(release).encode()), \
             patch.object(sources, 'https_open', side_effect=lambda url: Response(payload)) as fetch:
            folder = Path(temporary)
            result, actual = sources.github_image('owner/repo', 'latest', folder, lambda *args: None)
            self.assertEqual(actual, digest)
            self.assertEqual(result.read_bytes(), payload)
            sources.github_image('owner/repo', 'latest', folder, lambda *args: None)
            self.assertEqual(fetch.call_count, 1)
            result.unlink()
            release['assets'][0]['digest'] = 'sha256:' + '0' * 64
            with patch.object(sources, 'read_metadata', return_value=json.dumps(release).encode()), self.assertRaisesRegex(ValueError, 'checksum'):
                sources.github_image('owner/repo', 'latest', folder, lambda *args: None)
            self.assertEqual(list(folder.iterdir()), [])

    def test_github_uses_checksum_asset_and_ignores_unrelated_images(self):
        name = 'beitlab-robotics-milkv-duo-os-0.4.0-duos-arm64-sd.img.xz'
        release = {'assets': [{'name': 'other-board.img'}, {'name': name, 'browser_download_url': 'https://github.com/o/r/image'},
                              {'name': 'SHA256SUMS', 'browser_download_url': 'https://github.com/o/r/sums'}]}
        self.assertEqual(sources.release_asset(release)['name'], name)
        with patch.object(sources, 'read_metadata', side_effect=[json.dumps(release).encode(), ('0'*64 + '  wrong.img\n').encode()]), \
             self.assertRaisesRegex(ValueError, 'no entry'):
            sources.github_image('owner/repo', 'latest', Path('/not/created'), lambda *args: None)

    def test_authenticated_download_verifies_manifest_and_reuses_cache(self):
        payload = b'authenticated image fixture'
        digest = hashlib.sha256(payload).hexdigest()
        name = 'beitlab-robotics-milkv-duo-os-0.4.0-duos-arm64-sd.img.xz'
        prefix = 'https://api.github.com/repos/owner/repo/releases/assets/'
        release = {'isDraft': True, 'assets': [
            {'name': name, 'apiUrl': prefix + '1', 'size': len(payload)},
            {'name': 'SHA256SUMS', 'apiUrl': prefix + '2', 'size': 200}]}
        calls = []

        def gh(argv, **kwargs):
            calls.append(argv)
            if argv[1:3] == ['release', 'view']:
                self.assertEqual(argv[-2:], ['--', 'v0.4.0'])
                output = json.dumps(release).encode()
            else:
                self.assertEqual(argv[1], 'api')
                self.assertIn('Accept: application/octet-stream', argv)
                self.assertEqual(kwargs['env']['GH_HOST'], 'github.com')
                output = (digest + '  ' + name + '\n').encode() if argv[2].endswith('/2') else payload
            kwargs['stdout'].write(output)
            return subprocess.CompletedProcess(argv, 0)

        with tempfile.TemporaryDirectory() as temporary, patch.object(sources.shutil, 'which', return_value='/usr/bin/gh'), \
             patch.object(sources.subprocess, 'run', side_effect=gh), patch.object(sources, 'https_open') as anonymous:
            folder = Path(temporary)
            result, actual = sources.github_image('owner/repo', 'v0.4.0', folder, lambda *args: None, authenticated=True)
            self.assertEqual(actual, digest)
            self.assertEqual(result.read_bytes(), payload)
            sources.github_image('owner/repo', 'v0.4.0', folder, lambda *args: None, authenticated=True)
            self.assertEqual(sum(call[1] == 'api' and call[2].endswith('/1') for call in calls), 1)
            anonymous.assert_not_called()
            result.unlink()
            payload = b'corrupt response'
            with self.assertRaisesRegex(ValueError, 'checksum'):
                sources.github_image('owner/repo', 'v0.4.0', folder, lambda *args: None, authenticated=True)
            self.assertEqual(list(folder.iterdir()), [])

    def test_authenticated_download_rejects_external_asset_and_oversize(self):
        for url in ('https://example.com/asset', 'https://api.github.com/repos/other/repo/releases/assets/1',
                    'https://api.github.com/repos/owner/repo/releases/assets/1?redirect=x'):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, 'outside'):
                sources.authenticated_asset('owner/repo', {'url': url}, 100)
        with self.assertRaisesRegex(ValueError, 'size limit'):
            sources.authenticated_asset('owner/repo', {
                'url': 'https://api.github.com/repos/owner/repo/releases/assets/1', 'size': 101}, 100)

    def test_authenticated_download_missing_login_and_metadata_limit(self):
        with patch.object(sources.shutil, 'which', return_value=None), self.assertRaisesRegex(ValueError, 'Install'):
            sources.authenticated_release('owner/repo', 'v0.4.0')
        with patch.object(sources.shutil, 'which', return_value='/usr/bin/gh'), \
             patch.object(sources.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)), \
             self.assertRaisesRegex(ValueError, 'gh auth login'):
            sources.authenticated_release('owner/repo', 'v0.4.0')

        def oversized(argv, **kwargs):
            kwargs['stdout'].write(b'x' * (1024 * 1024 + 1))
            return subprocess.CompletedProcess(argv, 0)

        with patch.object(sources.shutil, 'which', return_value='/usr/bin/gh'), \
             patch.object(sources.subprocess, 'run', side_effect=oversized), self.assertRaisesRegex(ValueError, 'size limit'):
            sources.authenticated_release('owner/repo', 'latest')

    def test_private_release_error_explains_authenticated_option(self):
        error = sources.urllib.error.HTTPError('https://api.github.com/test', 404, 'Not Found', {}, None)
        with patch.object(sources, 'read_metadata', side_effect=error), self.assertRaisesRegex(ValueError, '--github-auth'):
            sources.github_image('owner/repo', 'v0.4.0', Path('/not/created'), lambda *args: None)

    def test_http_and_downgrade_are_refused(self):
        with self.assertRaises(ValueError):
            sources.https_open('http://example.com/file.img')
        with self.assertRaises(ValueError):
            sources.HTTPSRedirect().redirect_request(None, None, 302, '', {}, 'http://example.com/file.img')

    def test_unpack_limit_and_corruption_clean_partial_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            image = folder / 'release.img.gz'
            image.write_bytes(gzip.compress(b'duo image'))
            output = sources.unpack(image, folder, lambda *args: None)
            self.assertEqual(output.read_bytes(), b'duo image')
            output.unlink()
            with patch.object(sources, 'MAX_IMAGE', 2), self.assertRaises(ValueError):
                sources.unpack(image, folder, lambda *args: None)
            self.assertFalse(output.exists())
            image.write_bytes(b'not gzip')
            with self.assertRaises(OSError):
                sources.unpack(image, folder, lambda *args: None)
            self.assertFalse(output.exists())

    def test_unpack_does_not_delete_an_existing_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            image = folder / 'release.img.gz'
            image.write_bytes(gzip.compress(b'duo image'))
            existing = folder / 'release.img'
            existing.write_bytes(b'keep this image')
            with self.assertRaises(FileExistsError):
                sources.unpack(image, folder, lambda *args: None)
            self.assertEqual(existing.read_bytes(), b'keep this image')


class UITest(unittest.TestCase):
    def test_worker_cancellation_is_reported_only_once(self):
        output = io.StringIO()

        def child(argv):
            print('Cancelled before writing. The drive was not changed.')
            return subprocess.CompletedProcess(argv, 130)

        with patch.object(CLI.sys, 'argv', ['beitlab-imager.py', '--no-emoji']), \
             patch.object(CLI, 'execute', side_effect=lambda *args: CLI.run_worker(['fake-worker'])), \
             patch.object(CLI.subprocess, 'run', side_effect=child), contextlib.redirect_stdout(output):
            self.assertEqual(CLI.main(), 130)
        self.assertEqual(output.getvalue().count('Cancelled'), 1)
        self.assertNotIn('If writing had begun', output.getvalue())

    def test_worker_reports_prewrite_cancellation_without_reflash_warning(self):
        output = io.StringIO()
        argv = ['beitlab-imager.py', '--no-emoji', '--_flash-worker', 'image.img', '/dev/sdc', '0' * 64, 'identity']
        with patch.object(CLI.sys, 'argv', argv), \
             patch.object(drives, 'flash', side_effect=drives.FlashCancelled(False)), contextlib.redirect_stdout(output):
            self.assertEqual(CLI.main(), 130)
        self.assertIn('The drive was not changed', output.getvalue())
        self.assertNotIn('again before booting', output.getvalue())

    def test_ssid_whitespace_is_preserved(self):
        with patch('builtins.input', return_value=' Lab network '):
            self.assertEqual(CLI.UI(False, False).ask('SSID', strip=False), ' Lab network ')

    def test_password_validation_and_confirmation_never_print_secret(self):
        ui = CLI.UI(False, False)
        secret = 'wifi-secret-value'
        output = io.StringIO()
        with patch.object(CLI.getpass, 'getpass', side_effect=[secret, 'different', secret, secret]), contextlib.redirect_stdout(output):
            result = ui.password('Wi-Fi password', lambda value: hashlib.sha256(value.encode()).hexdigest())
        self.assertEqual(result, hashlib.sha256(secret.encode()).hexdigest())
        self.assertNotIn(secret, output.getvalue())
        self.assertNotIn('different', output.getvalue())

    def test_password_refuses_getpass_echo_fallback(self):
        import warnings
        def echoed(*args):
            warnings.warn('echo fallback', CLI.getpass.GetPassWarning)
        with patch.object(CLI.getpass, 'getpass', side_effect=echoed), self.assertRaises(CLI.getpass.GetPassWarning):
            CLI.UI(False, False).password('Wi-Fi', lambda value: value)

    def test_device_terminal_escape_is_removed(self):
        self.assertNotIn('\x1b', CLI.plain('reader\x1b[31m\nmodel'))

    def test_local_argument_never_calls_github(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / 'local.img'
            image.write_bytes(b'local')
            args = CLI.argparse.Namespace(image=image, sha256=hashlib.sha256(b'local').hexdigest())
            with patch.object(sources, 'github_image') as download, contextlib.redirect_stdout(io.StringIO()):
                actual, _ = CLI.image_source(args, CLI.UI(False, False), Path(temporary))
            download.assert_not_called()
            self.assertEqual(actual, image)


@unittest.skipUnless(all(shutil.which(name) for name in ('mcopy', 'mdir', 'mmd', 'mkfs.vfat')), 'needs mtools/dosfstools (SDK container)')
class RealFATWizardTest(unittest.TestCase):
    def test_cli_prepares_image_over_a_real_terminal_without_touching_disks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fat = root / 'boot.vfat'
            with fat.open('wb') as stream:
                stream.truncate(64 * 1024 * 1024)
            subprocess.run(['mkfs.vfat', '-n', 'boot', str(fat)], check=True, capture_output=True)
            image = root / 'release.img'
            mbr = bytearray(512)
            mbr[450] = 0x0C
            struct.pack_into('<II', mbr, 454, 2048, fat.stat().st_size // 512)
            mbr[510:] = b'\x55\xaa'
            with image.open('wb') as output, fat.open('rb') as source:
                output.write(mbr); output.seek(2048*512); shutil.copyfileobj(source, output)
                output.write(b'root sentinel')
            drive = f'{image}@@{2048*512}'
            subprocess.run(['mmd', '-i', drive, '::/playground'], check=True)
            for marker in ('wifi-supported', 'setup-supported', 'user-supported'):
                path = root / marker; path.touch()
                subprocess.run(['mcopy', '-i', drive, str(path), f'::/playground/{marker}'], check=True)
            key = root / 'id_ed25519.pub'
            import base64
            key.write_text('ssh-ed25519 ' + base64.b64encode(struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + bytes(range(32))).decode() + ' test\n')
            digest = sources.digest_file(image)
            destination = root / 'personal.img'
            pid, fd = pty.fork()
            if pid == 0:
                script_directory = getattr(self, 'script_directory', SCRIPTS)
                os.execv(sys.executable, [sys.executable, str(script_directory/'beitlab-imager.py'), '--image', str(image),
                    '--sha256', digest, '--prepare-only', '--output', str(destination), '--no-color'])
            transcript = b''
            remaining = b''
            def expect(needle):
                nonlocal transcript, remaining
                deadline = time.monotonic() + 20
                while needle.encode() not in remaining:
                    if time.monotonic() > deadline:
                        self.fail(f'missing {needle}: {transcript!r}')
                    if select.select([fd], [], [], 0.1)[0]:
                        chunk = os.read(fd, 65536)
                        if not chunk:
                            self.fail(f'CLI exited early: {transcript!r}')
                        transcript += chunk; remaining += chunk
                remaining = remaining.split(needle.encode(), 1)[1]
            try:
                for prompt, answer in [('Configure Wi-Fi', '\n'), ('Wi-Fi SSID', 'Test Lab\n'),
                                       ('Wi-Fi country', 'CO\n'), ('Wi-Fi password (hidden):', 'private-test-password\n'),
                                       ('Confirm password (hidden):', 'private-test-password\n'),
                                       ('Hostname (without .local)', 'robot-01\n'), ('Choose access method', '1\n'),
                                       ('Public key file', str(key)+'\n')]:
                    expect(prompt); os.write(fd, answer.encode())
                expect('No disk was opened for writing.')
                waited, status = os.waitpid(pid, 0); pid = None
                self.assertEqual(os.waitstatus_to_exitcode(status), 0, transcript)
            finally:
                if pid is not None:
                    os.kill(pid, 9); os.waitpid(pid, 0)
                os.close(fd)
            self.assertNotIn(b'private-test-password', transcript)
            self.assertEqual(sources.digest_file(image), digest)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            expected = {'hostname': 'robot-01\n', 'authorized_keys': key.read_text(),
                        'user.conf': 'password_hash=\nssh_password_login=no\n'}
            for name, content in expected.items():
                result = subprocess.run(['mcopy', '-i', f'{destination}@@{2048*512}',
                                         f'::/playground/{name}', '-'], capture_output=True, text=True, check=True)
                self.assertEqual(result.stdout, content)
            wifi = subprocess.run(['mcopy', '-i', f'{destination}@@{2048*512}', '::/playground/wifi.conf', '-'],
                                  capture_output=True, text=True, check=True).stdout
            self.assertIn('ssid=' + b'Test Lab'.hex(), wifi)
            self.assertNotIn('private-test-password', wifi)
            self.assertEqual(sources.adjacent_checksum(destination), sources.digest_file(destination))


if __name__ == '__main__':
    unittest.main()
