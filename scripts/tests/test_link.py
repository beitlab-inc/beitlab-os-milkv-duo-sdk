"""beitlab-link (PROMPT.md 0.5.0): the job allowlist, the claim code, and the real link against a
fake device-hub on localhost with fake rtos-control, bl_send and board-status programs."""
import hashlib
import importlib.util
import json
import os
import queue
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LINK_PATH = Path(__file__).resolve().parents[2] / "link" / "beitlab-link.py"
SPEC = importlib.util.spec_from_file_location("beitlab_link", LINK_PATH)
LINK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LINK)

SHA = "a" * 64
SECRET = "0123456789abcdef" * 4


class Allowlist(unittest.TestCase):
    """Same vectors as robotics-studio services/device-hub/tests/test_contracts.py."""

    VALID = [
        {"type": "status"},
        {"type": "mcu.load", "args": {"elf": "elf_01", "sha256": SHA, "size": 4096}},
        {"type": "mcu.log", "args": {"follow": True, "seconds": 600}},
        {"type": "mcu.recover"}, {"type": "mcu.lock"}, {"type": "mcu.unlock"},
        {"type": "mcu.ping", "args": {"nonce": 7}},
        {"type": "mailbox.send", "args": {"id": 0x4F, "value": 0x7FFFFFFF}},
        {"type": "linux.put", "args": {"name": "two_cores_1", "script": "print('hi')\n", "sha256": SHA}},
        {"type": "linux.run", "args": {"name": "main", "timeout_s": 600}},
        {"type": "linux.stop", "args": {"name": "main"}},
        {"type": "hardware.inspect"},
    ]
    INVALID = [
        None, {}, {"type": "shell", "args": {"cmd": "rm -rf /"}}, {"type": "exec"}, {"type": "STATUS"},
        {"type": "status", "args": {"extra": 1}}, {"type": "status", "extra": 1},
        {"type": "mcu.load", "args": {"elf": "../x", "sha256": SHA, "size": 1}},
        {"type": "mcu.load", "args": {"elf": "e", "sha256": "A" * 64, "size": 1}},
        {"type": "mcu.load", "args": {"elf": "e", "sha256": SHA, "size": 4 * 1024 * 1024 + 1}},
        {"type": "mcu.log", "args": {"seconds": 601}}, {"type": "mcu.log", "args": {"follow": "yes"}},
        {"type": "mcu.ping", "args": {}}, {"type": "mcu.ping", "args": {"nonce": True}},
        {"type": "mcu.ping", "args": {"nonce": 0x80000000}},
        {"type": "mailbox.send", "args": {"id": 0x50, "value": 1}},
        {"type": "mailbox.send", "args": {"id": 1, "value": 0x80000000}},
        {"type": "linux.put", "args": {"name": "../evil", "script": "", "sha256": SHA}},
        {"type": "linux.put", "args": {"name": "m", "script": "x" * (256 * 1024 + 1), "sha256": SHA}},
        {"type": "linux.run", "args": {"name": "main; reboot"}},
        {"type": "linux.run", "args": {"name": "main", "timeout_s": 601}},
        {"type": "linux.stop", "args": {}},
        {"type": "hardware.inspect", "args": {"reboot": True}},
    ]

    def test_allowlist_is_exactly_the_contract(self):
        self.assertEqual(set(LINK.ALLOWLIST), {
            "status", "mcu.load", "mcu.log", "mcu.recover", "mcu.lock", "mcu.unlock", "mcu.ping",
            "mailbox.send", "linux.put", "linux.run", "linux.stop", "hardware.inspect"})

    def test_valid(self):
        for job in self.VALID:
            with self.subTest(job=job):
                self.assertEqual(LINK.check_job(job)[0], job["type"])

    def test_invalid(self):
        for job in self.INVALID:
            with self.subTest(job=job), self.assertRaises(LINK.Refused):
                LINK.check_job(job)


class Identity(unittest.TestCase):
    def test_claim_code_vectors(self):
        for secret, code in (("00" * 32, "ATPS24"), ("0123456789abcdef" * 4, "KPGFJP"), ("ff" * 32, "FW514V")):
            self.assertEqual(LINK.claim_code(secret), code)

    def test_studio_url_rules(self):
        for url in ("https://studio.beitlab.com", "http://192.168.1.116:8090", "http://10.0.0.5",
                    "http://beitlabgpu.local:8090", "http://gateway:8080"):
            self.assertTrue(LINK.valid_url(url), url)
        for url in ("http://studio.beitlab.com", "ftp://x", "file:///etc/passwd", "", "http://8.8.8.8"):
            self.assertFalse(LINK.valid_url(url), url)


# ---- the link against a fake hub ------------------------------------------------------------------

class FakeHub:
    def __init__(self):
        self.jobs: queue.Queue = queue.Queue()
        self.posts: list[tuple[str, dict, str]] = []
        self.results: dict[str, dict] = {}
        self.result_event = threading.Condition()
        self.elfs: dict[str, bytes] = {}
        self.refuse_auth = False
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code, body=None, raw=None):
                data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def auth_ok(self):
                return not hub.refuse_auth and self.headers.get("Authorization") == f"Device {SECRET}"

            def do_GET(self):
                if not self.auth_ok():
                    return self.reply(401, {"detail": "no"})
                if self.path.startswith("/v1/device/jobs?"):
                    try:
                        return self.reply(200, hub.jobs.get(timeout=0.3))
                    except queue.Empty:
                        return self.reply(204)
                if self.path.endswith("/elf"):
                    job_id = self.path.split("/")[4]
                    return self.reply(200, raw=hub.elfs[job_id])
                self.reply(404)

            def do_POST(self):
                if not self.auth_ok():
                    return self.reply(401, {"detail": "no"})
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                hub.posts.append((self.path, body, self.headers.get("Authorization")))
                if self.path.endswith("/result"):
                    with hub.result_event:
                        hub.results[self.path.split("/")[4]] = body
                        hub.result_event.notify_all()
                self.reply(200, {"claimed": True} if self.path.endswith("heartbeat") else {"ok": True})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def run(self, job, timeout=10):
        self.jobs.put(job)
        deadline = time.monotonic() + timeout
        with self.result_event:
            while job["id"] not in self.results:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AssertionError(f"no result for {job}")
                self.result_event.wait(left)
        return self.results[job["id"]]

    def output(self, job_id):
        return "".join(b["text"] for p, b, _ in self.posts if p == f"/v1/device/jobs/{job_id}/output")


FAKE_RTOS = """#!{py}
# The link runs tools with a minimal environment, so the fake finds its files next to itself.
import sys, os
log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rtos.log")
open(log, "a").write(" ".join(sys.argv[1:]) + "\\n")
cmd = sys.argv[1]
if cmd == "status":
    print("state=running\\nfirmware=/lib/firmware/playground.elf\\nlock=unlocked\\nrelease=0.5.0")
elif cmd == "load":
    data = sys.stdin.buffer.read()
    open(log + ".elf", "wb").write(data)
    print(f"loaded {{len(data)}} bytes")
elif cmd == "ping":
    print(f"host_ping: reply {{int(sys.argv[2]) + 1}}")
elif cmd == "log":
    import time
    for i in range(100):
        print(f"RT: tick {{i}}", flush=True); time.sleep(0.05)
else:
    print(cmd + " ok")
"""
FAKE_BL_SEND = """#!{py}
import sys
print(int(sys.argv[2]) * 2)
"""
FAKE_BOARD_STATUS = """#!{py}
import json, os
here = os.path.dirname(os.path.abspath(__file__))
open(os.path.join(here, "board-status.json"), "w").write(json.dumps({{"format": 1, "carrier": "found"}}))
print("inspection written")
"""


class LinkEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.hub = FakeHub()
        self.addCleanup(self.hub.server.server_close)
        self.addCleanup(self.hub.server.shutdown)

        def tool(name, text):
            p = root / name
            p.write_text(text.format(py=sys.executable))
            p.chmod(0o755)
            return str(p)

        (root / "url").write_text(self.hub.url + "\n")
        (root / "secret").write_text(SECRET + "\n")
        (root / "hardware").write_text("duos_standalone\n")
        (root / "release").write_text("beitlab Robotics MilkV duo OS 0.5.0\nsdk_commit=6f89\n")
        env = {
            "BEITLAB_LINK_URL_FILE": str(root / "url"),
            "BEITLAB_LINK_SECRET_FILE": str(root / "secret"),
            "BEITLAB_LINK_HARDWARE_FILE": str(root / "hardware"),
            "BEITLAB_LINK_RELEASE_FILE": str(root / "release"),
            "BEITLAB_LINK_STATUS_FILE": str(root / "run" / "status"),
            "BEITLAB_LINK_BOARD_STATUS": str(root / "board-status.json"),
            "BEITLAB_LINK_STUDIO_DIR": str(root / "studio"),
            "BEITLAB_LINK_STATE_DIR": str(root / "state"),
            "BEITLAB_LINK_RTOS_CONTROL": tool("rtos-control", FAKE_RTOS),
            "BEITLAB_LINK_BL_SEND": tool("bl_send", FAKE_BL_SEND),
            "BEITLAB_LINK_BOARD_STATUS_CMD": tool("board-status", FAKE_BOARD_STATUS),
            "BEITLAB_LINK_PYTHON": sys.executable,
            "BEITLAB_LINK_HOSTNAME": "robot-07",
            "BEITLAB_LINK_HEARTBEAT_S": "0.2",
            "BEITLAB_LINK_POLL_WAIT_S": "1",
        }
        self.root = root
        self.saved_env = dict(os.environ)
        os.environ.update(env)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self.saved_env)))
        self.start()

    def start(self):
        cfg = LINK.Config()
        self.link = LINK.Link(cfg, LINK.Hub(cfg.url(), cfg.secret()))
        self.thread = threading.Thread(target=self.link.run, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.link.stop.set()
        self.thread.join(timeout=5)

    def status_text(self):
        try:
            return (self.root / "run" / "status").read_text()
        except FileNotFoundError:
            return ""

    def wait_for(self, check, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if check():
                return True
            time.sleep(0.05)
        return False

    def test_heartbeat_says_what_the_board_is(self):
        self.assertTrue(self.wait_for(lambda: len([p for p in self.hub.posts if p[0].endswith("heartbeat")]) >= 2))
        path, body, auth = next(p for p in self.hub.posts if p[0].endswith("heartbeat"))
        self.assertEqual(auth, f"Device {SECRET}")
        self.assertEqual(body["hostname"], "robot-07")
        self.assertEqual(body["hardware"], "duos_standalone")
        self.assertEqual(body["release"], "0.5.0")
        self.assertEqual(body["rtos"]["state"], "running")
        self.assertNotIn("board_status", body)  # standalone image: no inspection
        self.assertTrue(self.wait_for(lambda: "state=online" in self.status_text()))

    def test_refused_job_is_reported_not_run(self):
        res = self.hub.run({"id": "j1", "type": "shell", "args": {"cmd": "id"}})
        self.assertFalse(res["ok"])
        self.assertIn("refused by the board", res["error"])
        res = self.hub.run({"id": "j2", "type": "mailbox.send", "args": {"id": 0x50, "value": 1}})
        self.assertFalse(res["ok"])
        self.assertNotIn("mailbox", (self.root / "rtos.log").read_text() if (self.root / "rtos.log").exists() else "")

    def test_mcu_commands_go_through_rtos_control(self):
        res = self.hub.run({"id": "j3", "type": "mcu.ping", "args": {"nonce": 7}})
        self.assertTrue(res["ok"])
        self.assertIn("reply 8", self.hub.output("j3"))
        self.assertTrue(self.hub.run({"id": "j4", "type": "mcu.recover"})["ok"])
        lines = (self.root / "rtos.log").read_text().splitlines()
        self.assertIn("ping 7", lines)
        self.assertIn("recover", lines)

    def test_mailbox_send(self):
        res = self.hub.run({"id": "j5", "type": "mailbox.send", "args": {"id": 0x41, "value": 21}})
        self.assertEqual(res["data"], {"reply": 42})

    def test_mcu_load_checks_size_and_hash(self):
        elf = b"\x7fELF" + b"\x00" * 60
        self.hub.elfs["j6"] = elf
        good = {"elf": "e1", "sha256": hashlib.sha256(elf).hexdigest(), "size": len(elf)}
        self.assertTrue(self.hub.run({"id": "j6", "type": "mcu.load", "args": good})["ok"])
        self.assertEqual((self.root / "rtos.log.elf").read_bytes(), elf)
        self.assertEqual((self.root / "state" / "last.elf").read_bytes(), elf)
        self.assertEqual(oct((self.root / "state" / "last.elf").stat().st_mode & 0o777), "0o600")
        self.hub.elfs["j7"] = elf + b"x"
        res = self.hub.run({"id": "j7", "type": "mcu.load", "args": good})
        self.assertFalse(res["ok"])
        self.hub.elfs["j8"] = b"\x00" * len(elf)
        res = self.hub.run({"id": "j8", "type": "mcu.load", "args": good})
        self.assertIn("sha256", res["error"])

    def test_linux_put_run_and_output(self):
        script = "import sys\nprint('hello from linux')\nprint('oops', file=sys.stderr)\nsys.exit(3)\n"
        put = {"name": "main", "script": script, "sha256": hashlib.sha256(script.encode()).hexdigest()}
        self.assertTrue(self.hub.run({"id": "j9", "type": "linux.put", "args": put})["ok"])
        path = self.root / "studio" / "main.py"
        self.assertEqual(path.read_text(), script)
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        res = self.hub.run({"id": "j10", "type": "linux.run", "args": {"name": "main", "timeout_s": 10}})
        self.assertEqual((res["ok"], res["exit_code"]), (False, 3))
        streams = {(b["stream"], b["text"]) for p, b, _ in self.hub.posts if p.endswith("j10/output")}
        self.assertIn(("stdout", "hello from linux\n"), streams)
        self.assertIn(("stderr", "oops\n"), streams)
        bad = dict(put, sha256="b" * 64)
        self.assertFalse(self.hub.run({"id": "j11", "type": "linux.put", "args": bad})["ok"])

    def test_linux_run_timeout_and_stop(self):
        script = "import time\nwhile True:\n    print('tick', flush=True)\n    time.sleep(0.1)\n"
        put = {"name": "loop", "script": script, "sha256": hashlib.sha256(script.encode()).hexdigest()}
        self.hub.run({"id": "j12", "type": "linux.put", "args": put})
        start = time.monotonic()
        res = self.hub.run({"id": "j13", "type": "linux.run", "args": {"name": "loop", "timeout_s": 1}})
        self.assertFalse(res["ok"])
        self.assertLess(time.monotonic() - start, 6)
        self.assertIn("stopped after 1 s", self.hub.output("j13"))
        self.hub.jobs.put({"id": "j14", "type": "linux.run", "args": {"name": "loop", "timeout_s": 60}})
        self.assertTrue(self.wait_for(lambda: "tick" in self.hub.output("j14")))
        self.assertTrue(self.hub.run({"id": "j15", "type": "linux.stop", "args": {"name": "loop"}})["ok"])
        with self.hub.result_event:
            self.hub.result_event.wait_for(lambda: "j14" in self.hub.results, timeout=10)
        self.assertFalse(self.hub.results["j14"]["ok"])
        self.assertIn("was stopped", self.hub.output("j14"))

    def test_inspect_refused_on_the_standalone_image(self):
        res = self.hub.run({"id": "j16", "type": "hardware.inspect"})
        self.assertFalse(res["ok"])
        self.assertIn("Robotics Board image", res["error"])

    def test_inspect_on_the_robotics_board_image_reloads_the_program(self):
        (self.root / "hardware").write_text("robotics_board_rev011\n")
        (self.root / "state").mkdir()
        (self.root / "state" / "last.elf").write_bytes(b"\x7fELFstudent")
        res = self.hub.run({"id": "j17", "type": "hardware.inspect"})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["data"]["board_status"], {"format": 1, "carrier": "found"})
        lines = (self.root / "rtos.log").read_text().splitlines()
        self.assertEqual([x for x in lines if x in ("recover", "load")], ["recover", "load"])
        self.assertEqual((self.root / "rtos.log.elf").read_bytes(), b"\x7fELFstudent")
        self.assertTrue(self.wait_for(lambda: any(
            p[0].endswith("heartbeat") and p[1].get("board_status") for p in self.hub.posts)))

    def test_revoked_board_backs_off(self):
        self.hub.refuse_auth = True
        self.assertTrue(self.wait_for(lambda: "state=unauthorized" in self.status_text()))


class Entry(unittest.TestCase):
    def test_no_studio_url_means_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = dict(os.environ)
            try:
                os.environ["BEITLAB_LINK_URL_FILE"] = f"{tmp}/missing"
                os.environ["BEITLAB_LINK_STATUS_FILE"] = f"{tmp}/status"
                self.assertEqual(LINK.main(["beitlab-link"]), 0)
                self.assertIn("state=disabled", Path(f"{tmp}/status").read_text())
            finally:
                os.environ.clear()
                os.environ.update(saved)

    def test_card_shows_the_claim_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = dict(os.environ)
            try:
                Path(f"{tmp}/secret").write_text(SECRET)
                Path(f"{tmp}/hw").write_text("duos_standalone")
                os.environ.update(BEITLAB_LINK_SECRET_FILE=f"{tmp}/secret", BEITLAB_LINK_HARDWARE_FILE=f"{tmp}/hw",
                                  BEITLAB_LINK_HOSTNAME="robot-07", BEITLAB_LINK_URL_FILE=f"{tmp}/none")
                text = LINK.card(LINK.Config())
                self.assertIn("Claim code:  KPGFJP", text)
                self.assertIn("Milk-V Duo S (standalone image)", text)
            finally:
                os.environ.clear()
                os.environ.update(saved)


if __name__ == "__main__":
    unittest.main()
