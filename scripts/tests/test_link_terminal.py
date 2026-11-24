"""Human terminal PTY tests on the host; not verified on hardware."""
import base64
import os
import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_link import LINK


class PtyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = SimpleNamespace(studio_dir=Path(self.tmp.name) / "studio")
        self.shell = LINK.PtyShell(self.cfg, 90, 30)
        self.addCleanup(self.shell.close)
        self.stop = threading.Event()

    def until(self, expected, timeout=5):
        output = b""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            output += self.shell.read()
            if expected in output:
                return output
            time.sleep(0.02)
        self.fail(f"PTY did not produce {expected!r}; got {output!r}")

    def test_shell_has_controlling_tty_utf8_and_minimal_environment(self):
        self.until(b"beitlab$ ")
        self.shell.write(b"printf '\\303\\261\\n'; test -t 0 && printf 'TTY_OK\\n'; pwd\n", self.stop)
        out = self.until(str(self.cfg.studio_dir).encode())
        self.assertIn("ñ".encode(), out)
        self.assertIn(b"TTY_OK", out)
        self.shell.write(b"env\n", self.stop)
        out = self.until(b"TERM=xterm-256color")
        self.assertNotIn(b"CREDENTIALS_DIRECTORY", out)
        self.assertNotIn(b"BEITLAB_LINK_SECRET", out)

    def test_resize_ctrl_c_exit_and_foreground_cleanup(self):
        self.until(b"beitlab$ ")
        self.shell.resize(110, 35)
        self.shell.write(b"stty size\n", self.stop)
        self.until(b"35 110")
        self.shell.write(b"sleep 60\n", self.stop)
        time.sleep(0.15)
        group = os.tcgetpgrp(self.shell.master)
        self.assertNotEqual(group, self.shell.proc.pid)
        self.shell.write(b"\x03", self.stop)
        self.until(b"beitlab$ ")
        self.shell.write(b"sleep 60\n", self.stop)
        time.sleep(0.15)
        group = os.tcgetpgrp(self.shell.master)
        self.shell.close()
        self.assertIsNotNone(self.shell.proc.returncode)
        # A killed orphan can briefly be a zombie until the host init reaps it.
        state = Path(f"/proc/{group}/stat")
        self.assertTrue(not state.exists() or state.read_text().split()[2] == "Z")

    def test_size_bounds(self):
        for cols, rows in ((1, 20), (301, 20), (80, 121), (True, 20)):
            with self.assertRaises(ValueError):
                self.shell.resize(cols, rows)


class RelayTests(unittest.TestCase):
    def test_exchange_retries_output_and_deduplicates_input_then_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = SimpleNamespace(studio_dir=Path(tmp) / "studio")
            stop = threading.Event()
            sid = "12345678-1234-1234-1234-123456789012"
            session = {"id": sid, "cols": 80, "rows": 24}
            posts, created = [], []
            real_shell = LINK.PtyShell

            def shell(*args):
                obj = real_shell(*args)
                created.append(obj)
                return obj

            class Hub:
                failed = False

                def request(self, method, path, body, **kw):
                    posts.append(body)
                    if body["data"] and not self.failed:
                        self.failed = True
                        raise LINK.HubError(0, "network interruption")
                    if body["exited"]:
                        stop.set()
                        return {"session": None}
                    return {"session": session, "output_ack": body["output_seq"], "events": [
                        {"seq": 1, "kind": "input", "data": base64.b64encode(b"printf 'RELAY_OK\\n'; exit\n").decode()}
                    ]}

            with patch.object(LINK, "PtyShell", shell):
                thread = threading.Thread(target=LINK.terminal_loop, args=(cfg, Hub(), stop))
                thread.start()
                thread.join(7)
                stop.set()
                thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(created), 1)
            self.assertIsNotNone(created[0].proc.returncode)
            chunks = {p["output_seq"]: base64.b64decode(p["data"]) for p in posts if p["data"]}
            self.assertIn(b"RELAY_OK", b"".join(chunks.values()))
            self.assertTrue(any(a["data"] and a["data"] == b["data"] and a["output_seq"] == b["output_seq"]
                                for a, b in zip(posts, posts[1:])))

    def test_network_watchdog_closes_a_silent_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = SimpleNamespace(studio_dir=Path(tmp) / "studio")
            stop, created = threading.Event(), []
            real_shell = LINK.PtyShell

            def shell(*args):
                obj = real_shell(*args)
                created.append(obj)
                return obj

            class Hub:
                def request(self, method, path, body, **kw):
                    if body["session_id"] is None:
                        return {"session": {"id": "12345678-1234-1234-1234-123456789012", "cols": 80, "rows": 24}}
                    raise LINK.HubError(0, "offline")

            with patch.object(LINK, "PtyShell", shell), patch.object(LINK, "TERMINAL_LOST_S", 0.1):
                thread = threading.Thread(target=LINK.terminal_loop, args=(cfg, Hub(), stop))
                thread.start()
                end = time.monotonic() + 4
                while time.monotonic() < end and (not created or created[0].proc.poll() is None):
                    time.sleep(0.05)
                stop.set()
                thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(created), 1)
            self.assertIsNotNone(created[0].proc.returncode)


if __name__ == "__main__":
    unittest.main()
