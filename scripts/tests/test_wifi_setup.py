#!/usr/bin/env python3
"""Exercise the real BusyBox wizard over a PTY with isolated fake hardware.

Run: python3 -m unittest discover -s scripts/tests -v
Set WIFI_TEST_WPA_PASSPHRASE to a native wpa_passphrase binary to exercise
the real password-to-PSK conversion instead of the mock (required for the
wpa_supplicant 2.11 pipe-input regression).
No network devices, services, or host configuration are changed.
"""
import errno
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "image/overlay/usr/bin/beitlab-wifi-setup"
MOCK = r'''#!/usr/bin/env python3
import hashlib, json, os, pathlib, sys
root = pathlib.Path(os.environ['WIFI_TEST_ROOT'])
state_path = root / 'state.json'
s = json.loads(state_path.read_text())
cmd = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with (root / 'commands.jsonl').open('a') as f:
    f.write(json.dumps([cmd, *args]) + '\n')
if cmd == 'id':
    print(0)
elif cmd == 'sleep':
    pass
elif cmd == 'systemctl':
    if args[0] == 'restart':
        s['restarts'] = s.get('restarts', 0) + 1
        state_path.write_text(json.dumps(s))
elif cmd == 'dhcpcd':
    pass
elif cmd == 'wpa_passphrase':
    if os.environ.get('WIFI_TEST_WPA_PASSPHRASE'):
        os.execv(os.environ['WIFI_TEST_WPA_PASSPHRASE'], ['wpa_passphrase', *args])
    password = sys.stdin.readline().rstrip('\n')
    psk = hashlib.pbkdf2_hmac('sha1', password.encode(), args[0].encode(), 4096, 32).hex()
    print('network={\n\t#psk="' + password + '"\n\tpsk=' + psk + '\n}')
elif cmd == 'wpa_cli':
    if args[-1] == 'status':
        connected = s.get('restarts', 0) >= s.get('succeed_after', 999)
        print('wpa_state=' + ('COMPLETED' if connected else 'DISCONNECTED'))
    else:
        print('OK')
elif cmd == 'ip':
    if 'flush' not in args:
        iface = args[args.index('dev')+1]
        if iface in ('eth0', 'end0') and s.get('ethernet'):
            print('    inet 192.0.2.2/24 scope global ' + iface)
        elif iface == 'wlan0' and s.get('restarts', 0) >= s.get('succeed_after', 999):
            print('    inet 192.0.2.3/24 scope global wlan0')
        elif s.get('link_local'):
            print('    inet 169.254.1.2/16 scope global ' + iface)
'''


class Console:
    def __init__(self, root, args=()):
        self.output = b""
        self.transcript = b""
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            env = dict(os.environ, WIFI_TEST_ROOT=str(root), PATH=f"{root}/bin:" + os.environ["PATH"])
            # The wrapper records terminal flags after the wizard exits.
            os.execve(shutil.which("busybox"), ["busybox", "sh", "-c",
                'trap ":" INT; busybox sh "$@"; result=$?; stty -g > "$WIFI_TEST_ROOT/tty-after"; exit "$result"',
                "sh", str(root / "wizard"), *args], env)

    def expect(self, text, timeout=8):
        end = time.monotonic() + timeout
        needle = text.encode()
        while needle not in self.output:
            if time.monotonic() >= end:
                raise AssertionError(f"Missing {text!r}: {self.output.decode(errors='replace')}")
            if select.select([self.fd], [], [], 0.1)[0]:
                try:
                    chunk = os.read(self.fd, 65536)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    chunk = b""
                if not chunk:
                    raise AssertionError(f"Console exited before {text!r}: {self.output!r}")
                self.output += chunk
                self.transcript += chunk
        # Consume matching text so repeated prompts require new output.
        self.output = self.output.split(needle, 1)[1]

    def send(self, text):
        os.write(self.fd, text.encode())

    def finish(self, timeout=8):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.pid = None
                return os.waitstatus_to_exitcode(status)
            time.sleep(0.02)
        raise AssertionError("wizard did not exit")

    def close(self):
        if self.pid:
            os.kill(self.pid, signal.SIGKILL)
            os.waitpid(self.pid, 0)
        os.close(self.fd)


@unittest.skipUnless(shutil.which("busybox"), "needs host BusyBox")
class WifiSetupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("config", "run", "net/eth0", "net/wlan0", "bin"):
            (self.root / name).mkdir(parents=True)
        (self.root / "net/eth0/carrier").write_text("0\n")
        self.config = self.root / "config/beitlab-wlan0.conf"
        self.original = "ctrl_interface=/run/wpa_supplicant\n# previous working profile\n"
        self.config.write_text(self.original)
        self.config.chmod(0o600)
        self.state()
        for name in ("id", "ip", "wpa_cli", "wpa_passphrase", "systemctl", "dhcpcd", "sleep"):
            path = self.root / "bin" / name
            path.write_text(MOCK)
            path.chmod(0o755)
        script = SCRIPT.read_text().replace("/etc/wpa_supplicant", str(self.root / "config"))
        script = script.replace("/sys/class/net", str(self.root / "net"))
        script = script.replace("/run/beitlab-wifi-setup.lock", str(self.root / "run/lock"))
        # Debian's host BusyBox may prefer built-in applets over PATH.
        for name in ("id", "ip", "sleep"):
            script = script.replace(name + " ", str(self.root / "bin" / name) + " ")
        # Accelerate bounded read timers, still exercising the BusyBox builtin.
        script = script.replace('read -r -t 1 ANSWER', 'read -r -t 0.01 ANSWER')
        script = script.replace("(30 seconds): ' 30", "(30 seconds): ' 0.05")
        (self.root / "wizard").write_text(script)

    def state(self, **kwargs):
        (self.root / "state.json").write_text(json.dumps(kwargs))

    def console(self, auto=False):
        console = Console(self.root, ["--auto"] if auto else [])
        self.addCleanup(console.close)
        return console

    def credentials(self, console, ssid="Lab network", password="secret123"):
        console.expect('SSID (q to cancel): ')
        console.send(ssid + "\n")
        console.expect('Password (hidden; Ctrl-C to cancel): ')
        console.send(password + "\n")

    def test_ethernet_skips_prompt(self):
        self.state(ethernet=True)
        (self.root / "net/eth0/carrier").write_text("1\n")
        self.assertEqual(self.console(auto=True).finish(), 0)
        self.assertEqual(self.config.read_text(), self.original)

    def test_saved_wifi_skips_prompt(self):
        self.state(succeed_after=0)
        self.assertEqual(self.console(auto=True).finish(), 0)

    def test_systemd_ethernet_name_skips_prompt(self):
        self.state(ethernet=True)
        (self.root / 'net/end0').mkdir()
        (self.root / 'net/end0/carrier').write_text('1\n')
        self.assertEqual(self.console(auto=True).finish(), 0)

    def test_unattended_boot_times_out(self):
        console = self.console(auto=True)
        console.expect('Connect to Wi-Fi now?')
        self.assertEqual(console.finish(), 0)
        self.assertEqual(self.config.read_text(), self.original)

    def test_link_local_is_not_a_connection_and_decline_is_safe(self):
        self.state(link_local=True)
        console = self.console(auto=True)
        console.expect('Connect to Wi-Fi now?')
        console.send('n\n')
        self.assertEqual(console.finish(), 0)
        self.assertEqual(self.config.read_text(), self.original)

    def test_success_saves_safe_ssid_and_hashed_password(self):
        self.state(succeed_after=1)
        console = self.console()
        console.expect('Configure Wi-Fi now?')
        console.send('y\n')
        ssid = 'Lab "quote" \\ $room'
        secret = 'not-in-logs-$123'
        self.credentials(console, ssid, secret)
        console.expect('Wi-Fi connected.')
        self.assertEqual(console.finish(), 0)
        self.assertNotIn(secret.encode(), console.transcript)
        config = self.config.read_text()
        self.assertIn('ssid=' + ssid.encode().hex(), config)
        self.assertNotIn(secret, config)
        self.assertNotIn(secret, (self.root / 'commands.jsonl').read_text())
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list((self.root / 'config').glob('.beitlab-*')), [])

    def test_three_failures_then_new_credentials(self):
        self.state(succeed_after=2)
        console = self.console()
        console.expect('Configure Wi-Fi now?')
        console.send('y\n')
        self.credentials(console)
        for attempt in (1, 2, 3):
            console.expect(f'Connecting (attempt {attempt}/3;')
        console.expect('Try another SSID/password?')
        console.send('y\n')
        self.credentials(console, 'Second network')
        console.expect('Wi-Fi connected.')
        self.assertEqual(console.finish(), 0)
        self.assertIn('ssid=' + b'Second network'.hex(), self.config.read_text())

    def test_cancel_during_connection_restores_profile(self):
        console = self.console()
        console.expect('Configure Wi-Fi now?')
        console.send('y\n')
        self.credentials(console)
        console.expect('Connecting (attempt 1/3;')
        console.send('q\n')
        self.assertEqual(console.finish(), 0)
        self.assertEqual(self.config.read_text(), self.original)

    def test_ctrl_c_restores_password_echo(self):
        console = self.console()
        console.expect('Configure Wi-Fi now?')
        console.send('y\n')
        console.expect('SSID (q to cancel): ')
        console.send('Lab\n')
        console.expect('Password (hidden; Ctrl-C to cancel): ')
        console.send('\x03')
        self.assertEqual(console.finish(), 0)
        import termios
        self.assertTrue(termios.tcgetattr(console.fd)[3] & termios.ECHO)

    def test_board_without_wifi_exits_cleanly(self):
        (self.root / 'net/wlan0').rmdir()
        console = self.console()
        console.expect('Configure Wi-Fi now?')
        console.send('y\n')
        console.expect('No wlan0 device found.')
        self.assertEqual(console.finish(), 1)


if __name__ == '__main__':
    unittest.main()
