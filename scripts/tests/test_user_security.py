#!/usr/bin/env python3
"""Exercise real BusyBox privilege boundaries and isolated account imports."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("configure_wifi", REPO / "scripts/configure-wifi.py")
WIFI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WIFI)


class PasswordProfileTest(unittest.TestCase):
    def test_password_hash_and_explicit_login_choice(self):
        password = "a unique test password"
        config = WIFI.user_profile(password, True)
        self.assertNotIn(password, config)
        self.assertIn("password_hash=$6$", config)
        self.assertTrue(config.endswith("ssh_password_login=yes\n"))
        self.assertNotEqual(config, WIFI.user_profile(password, True))
        self.assertEqual(WIFI.user_profile(), "password_hash=\nssh_password_login=no\n")

    def test_weak_password_and_password_login_without_password_rejected(self):
        for password in ("short", "a" * 129, "a long password\n"):
            with self.assertRaises(ValueError):
                WIFI.user_profile(password)
        with self.assertRaises(ValueError):
            WIFI.user_profile(None, True)


@unittest.skipUnless(shutil.which("busybox"), "needs BusyBox")
class AccountImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("boot/playground", "etc/ssh/sshd_config.d", "var/lib", "bin"):
            (self.root / name).mkdir(parents=True)
        self.shadow = self.root / "etc/shadow"
        self.original = "root:!:1:0:99999:7:::\nbeitlab:*:1:0:99999:7:::\n"
        self.shadow.write_text(self.original)
        mock = self.root / "bin/chpasswd"
        mock.write_text('''#!/usr/bin/env python3
import os,pathlib,sys
r=pathlib.Path(os.environ['ACCOUNT_TEST_ROOT'])
name,hashed=sys.stdin.read().strip().split(':')
assert name=='beitlab'
p=r/'etc/shadow'
rows=p.read_text().splitlines()
p.write_text('\\n'.join(name+':'+hashed+':1:0:99999:7:::' if line.startswith(name+':') else line for line in rows)+'\\n')
''')
        mock.chmod(0o755)
        script = (REPO / "image/overlay/usr/libexec/playground/import-user").read_text()
        for prefix in ("/boot/playground", "/etc/ssh", "/etc/shadow", "/var/lib/beitlab"):
            script = script.replace(prefix, str(self.root) + prefix)
        script = script.replace("chpasswd -e", str(mock) + " -e")
        script = script.replace("\nsync\n", "\n:\n").replace("    sync\n", "    :\n")
        self.script = self.root / "import-user"
        self.script.write_text(script)
        self.source = self.root / "boot/playground/user.conf"

    def run_import(self):
        return subprocess.run(["busybox", "sh", str(self.script)],
                              env=dict(os.environ, ACCOUNT_TEST_ROOT=str(self.root)),
                              capture_output=True, text=True)

    def test_unique_user_password_and_root_stays_locked(self):
        config = WIFI.user_profile("a unique test password", True)
        self.source.write_text(config)
        result = self.run_import()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("root:!:", self.shadow.read_text())
        self.assertIn(config.splitlines()[0].split("=", 1)[1], self.shadow.read_text())
        self.assertFalse(self.source.exists())
        self.assertTrue((self.root / "var/lib/beitlab/password-set").is_file())
        self.assertEqual((self.root / "etc/ssh/sshd_config.d/40-beitlab-user.conf").read_text(), "PasswordAuthentication yes\n")
        self.assertNotIn("$6$", result.stdout + result.stderr)

    def test_key_only_account_unlocked_with_unknown_password(self):
        key = self.root / "id"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        shutil.copyfile(str(key) + ".pub", self.root / "boot/playground/authorized_keys")
        self.source.write_text(WIFI.user_profile())
        result = self.run_import()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("beitlab:$6$", self.shadow.read_text())
        self.assertIn("root:!:", self.shadow.read_text())
        self.assertFalse((self.root / "var/lib/beitlab/password-set").exists())
        before = self.shadow.read_text()
        self.assertEqual(self.run_import().returncode, 0)
        self.assertEqual(self.shadow.read_text(), before)

    def test_invalid_or_injected_boot_settings_do_not_change_shadow(self):
        for value in ("password_hash=bad\nssh_password_login=no\n",
                      "password_hash=\nssh_password_login=yes\n",
                      "password_hash=\nssh_password_login=no\nPermitRootLogin=yes\n",
                      "$(touch /tmp/should-not-exist)\nssh_password_login=no\n"):
            self.source.write_text(value)
            result = self.run_import()
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.shadow.read_text(), self.original)
            self.assertTrue(self.source.exists())


@unittest.skipUnless(shutil.which("busybox"), "needs BusyBox")
class PrivilegeBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        mock = self.root / "agent"
        mock.write_text('''#!/usr/bin/env python3
import json,os,pathlib,sys
r=pathlib.Path(os.environ['CONTROL_TEST_ROOT'])
(r/'calls').write_text(json.dumps(sys.argv[1:]))
if sys.argv[1]=='load':
 p=pathlib.Path(sys.argv[2])
 assert p.stat().st_mode & 0o777 == 0o600
 (r/'received').write_bytes(p.read_bytes())
sys.exit(int(os.environ.get('CONTROL_TEST_RC','0')))
''')
        mock.chmod(0o755)
        script = (REPO / "image/overlay/usr/libexec/playground/rtos-control").read_text()
        script = script.replace("/usr/bin/rtos-agent", str(mock)).replace("/usr/bin/host_ping", str(mock))
        script = script.replace("/run/rtos-upload.", str(self.root / "rtos-upload."))
        self.script = self.root / "control"
        self.script.write_text(script)

    def run_control(self, *args, data=b"", rc=0):
        return subprocess.run(["busybox", "sh", str(self.script), *args], input=data,
                              env=dict(os.environ, CONTROL_TEST_ROOT=str(self.root), CONTROL_TEST_RC=str(rc)),
                              capture_output=True)

    def test_reject_arbitrary_paths_shells_and_options(self):
        for args in (("boot",), ("sh",), ("load", "/etc/shadow"), ("status", "extra"),
                     ("log", "--help"), ("ping", "$(id)"), ("ping", "4294967296")):
            self.assertEqual(self.run_control(*args).returncode, 2, args)
            self.assertFalse((self.root / "calls").exists())

    def test_stdin_is_staged_privately_and_cleaned_on_agent_failure(self):
        data = b"ELF test bytes" * 10000
        result = self.run_control("load", data=data, rc=3)
        self.assertEqual(result.returncode, 3)
        self.assertEqual((self.root / "received").read_bytes(), data)
        self.assertEqual(list(self.root.glob("rtos-upload.*")), [])

    def test_oversized_upload_never_reaches_agent(self):
        self.assertEqual(self.run_control("load", data=b"x" * (33 * 1024 * 1024)).returncode, 2)
        self.assertFalse((self.root / "calls").exists())
        self.assertEqual(list(self.root.glob("rtos-upload.*")), [])


@unittest.skipUnless(shutil.which("busybox"), "needs BusyBox")
class SerialUserSetupTest(unittest.TestCase):
    def setUp(self):
        from test_wifi_setup import Console
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "bin").mkdir()
        mock = self.root / "bin/chpasswd"
        mock.write_text('#!/bin/sh\ncat > "$WIFI_TEST_ROOT/chpasswd-input"\n')
        mock.chmod(0o755)
        script = (REPO / "image/overlay/usr/bin/beitlab-user-setup").read_text()
        script = script.replace("/var/lib/beitlab", str(self.root / "state"))
        script = script.replace("chpasswd -e", str(mock) + " -e")
        (self.root / "wizard").write_text(script)
        self.console = Console(self.root)
        self.addCleanup(self.console.close)

    def enter_passwords(self, password, confirm):
        self.console.expect("(30 seconds): ")
        self.console.send("y\n")
        self.console.expect("at least 12 characters): ")
        self.console.send(password + "\n")
        self.console.expect("Confirm password: ")
        self.console.send(confirm + "\n")

    def test_private_password_setup_and_terminal_echo_restored(self):
        import termios
        password = "unique serial password"
        self.enter_passwords(password, password)
        self.console.expect("Password set.")
        self.assertEqual(self.console.finish(), 0)
        self.assertNotIn(password.encode(), self.console.transcript)
        self.assertTrue((self.root / "state/password-set").is_file())
        self.assertTrue((self.root / "chpasswd-input").read_text().startswith("beitlab:$6$"))
        self.assertTrue(termios.tcgetattr(self.console.fd)[3] & termios.ECHO)

    def test_weak_password_does_not_initialize_account(self):
        self.enter_passwords("weak", "weak")
        self.assertEqual(self.console.finish(), 1)
        self.assertFalse((self.root / "state/password-set").exists())
        self.assertFalse((self.root / "chpasswd-input").exists())


if __name__ == "__main__":
    unittest.main()
