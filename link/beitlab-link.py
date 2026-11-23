#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""beitlab-link: connects this board to the beitlab Robotics Studio (PROMPT.md, 0.5.0).

The board connects outbound to the Studio's device-hub over HTTPS (plain HTTP only for a lab URL)
and long-polls for jobs. Nothing listens on the board. Only the jobs in ALLOWLIST run; anything
else is refused and logged. The C906 is reached only through the restricted `rtos-control` sudo
helper and `bl_send`, so this program needs no privilege of its own. Standard library only.

  beitlab-link            run (beitlab-link.service)
  beitlab-link --card     print the board card: hostname, board type, claim code
  beitlab-link --motd     print one status line for the login message
  beitlab-link --claim-code  print the claim code (import-studio writes it for J22 and login)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import shlex
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VERSION = "0.5.0"
log = logging.getLogger("beitlab-link")

HEARTBEAT_S = 10
POLL_WAIT_S = 25
MAX_JOBS = 8
MAX_ELF_BYTES = 4 * 1024 * 1024
MAX_SCRIPT_BYTES = 256 * 1024
MAX_CHUNK = 16 * 1024
HARDWARE = ("duos_standalone", "robotics_board_rev011")
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


# ---- settings -------------------------------------------------------------------------------------
# Production uses the defaults. The environment overrides exist for tools/mock_board and the tests;
# none of them comes from the server.

def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default))


class Config:
    def __init__(self) -> None:
        home = Path(os.environ.get("HOME", "/home/beitlab"))
        creds = os.environ.get("CREDENTIALS_DIRECTORY", "/run/credentials/beitlab-link.service")
        self.url_file = _env_path("BEITLAB_LINK_URL_FILE", "/etc/beitlab/studio/url")
        self.secret_file = _env_path("BEITLAB_LINK_SECRET_FILE", f"{creds}/device-secret")
        self.hardware_file = _env_path("BEITLAB_LINK_HARDWARE_FILE", "/etc/beitlab/hardware")
        self.release_file = _env_path("BEITLAB_LINK_RELEASE_FILE", "/etc/duo-playground-release")
        self.status_file = _env_path("BEITLAB_LINK_STATUS_FILE",
                                     os.environ.get("RUNTIME_DIRECTORY", "/run/beitlab-link") + "/status")
        self.board_status_file = _env_path("BEITLAB_LINK_BOARD_STATUS", "/run/beitlab/board-status.json")
        self.studio_dir = _env_path("BEITLAB_LINK_STUDIO_DIR", str(home / "studio"))
        self.state_dir = _env_path("BEITLAB_LINK_STATE_DIR",
                                   os.environ.get("STATE_DIRECTORY", str(home / ".local/state/beitlab-link")))
        self.rtos_control = shlex.split(os.environ.get(
            "BEITLAB_LINK_RTOS_CONTROL", "sudo -n /usr/libexec/playground/rtos-control"))
        self.bl_send = shlex.split(os.environ.get("BEITLAB_LINK_BL_SEND", "/usr/bin/bl_send"))
        self.board_status_cmd = shlex.split(os.environ.get(
            "BEITLAB_LINK_BOARD_STATUS_CMD", "/usr/bin/beitlab-board-status"))
        self.python = os.environ.get("BEITLAB_LINK_PYTHON", "/usr/bin/python3")
        self.hostname = os.environ.get("BEITLAB_LINK_HOSTNAME") or socket.gethostname()
        self.heartbeat_s = float(os.environ.get("BEITLAB_LINK_HEARTBEAT_S", HEARTBEAT_S))
        self.poll_wait_s = int(os.environ.get("BEITLAB_LINK_POLL_WAIT_S", POLL_WAIT_S))

    def read(self, path: Path, default: str = "") -> str:
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return default

    def url(self) -> str:
        return self.read(self.url_file).rstrip("/")

    def secret(self) -> str:
        return self.read(self.secret_file).lower()

    def hardware(self) -> str:
        hw = self.read(self.hardware_file)
        return hw if hw in HARDWARE else "unknown"

    def release(self) -> str:
        text = self.read(self.release_file)
        m = re.search(r"(?m)^(?:version|VERSION)\s*=\s*\"?([^\s\"]+)", text)
        if m:
            return m.group(1)[:64]
        m = re.search(r"\b(\d+\.\d+\.\d+)\b", text)
        return m.group(1) if m else ""


def valid_url(url: str) -> bool:
    """https://, or http:// to a private-network host (the lab Compose stack)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return True
    if parts.scheme != "http" or not parts.hostname:
        return False
    host = parts.hostname
    if host.endswith(".local") or host == "localhost" or "." not in host:
        return True
    return bool(re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|127\.)", host))


# ---- claim code (same derivation and vectors as robotics-studio device-hub) ----------------------

def secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("ascii")).hexdigest()


def claim_code(secret: str) -> str:
    digest = hashlib.sha256(b"beitlab-claim-v1" + bytes.fromhex(secret_hash(secret))).digest()
    bits = int.from_bytes(digest[:4], "big") >> 2
    return "".join(CROCKFORD[(bits >> shift) & 31] for shift in range(25, -1, -5))


# ---- job allowlist (independent copy of robotics-studio device_hub/jobs.py) -----------------------

class Refused(ValueError):
    pass


NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
ARTIFACT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _int(args: dict, key: str, lo: int, hi: int, default: int | None = None) -> int:
    v = args.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise Refused(f"{key} must be an integer in {lo}..{hi}")
    return v


def _str(args: dict, key: str, pattern: re.Pattern) -> str:
    v = args.get(key)
    if not isinstance(v, str) or not pattern.match(v):
        raise Refused(f"{key} is not valid")
    return v


def _only(args: dict, *keys: str) -> None:
    extra = set(args) - set(keys)
    if extra:
        raise Refused(f"unexpected argument {sorted(extra)[0]!r}")


def check_job(job: object) -> tuple[str, dict]:
    """Validate a job from the hub. Returns (type, normalized args) or raises Refused."""
    if not isinstance(job, dict) or set(job) - {"id", "type", "args"}:
        raise Refused("malformed job")
    kind, args = job.get("type"), job.get("args", {})
    if not isinstance(kind, str) or kind not in ALLOWLIST:
        raise Refused(f"job type {str(kind)[:40]!r} is not allowed")
    if not isinstance(args, dict):
        raise Refused("args must be an object")
    return kind, ALLOWLIST[kind](args)


def _no_args(args: dict) -> dict:
    _only(args)
    return {}


def _mcu_load(args: dict) -> dict:
    _only(args, "elf", "sha256", "size")
    return {"elf": _str(args, "elf", ARTIFACT_RE), "sha256": _str(args, "sha256", SHA_RE),
            "size": _int(args, "size", 1, MAX_ELF_BYTES)}


def _mcu_log(args: dict) -> dict:
    _only(args, "follow", "seconds")
    follow = args.get("follow", False)
    if not isinstance(follow, bool):
        raise Refused("follow must be true or false")
    return {"follow": follow, "seconds": _int(args, "seconds", 1, 600, 10)}


def _mcu_ping(args: dict) -> dict:
    _only(args, "nonce")
    return {"nonce": _int(args, "nonce", 0, 0x7FFFFFFF)}


def _mailbox_send(args: dict) -> dict:
    _only(args, "id", "value")
    return {"id": _int(args, "id", 0x00, 0x4F), "value": _int(args, "value", 0, 0x7FFFFFFF)}


def _linux_put(args: dict) -> dict:
    _only(args, "name", "script", "sha256")
    script = args.get("script")
    if not isinstance(script, str) or len(script) > MAX_SCRIPT_BYTES:
        raise Refused("script must be text of at most 256 KiB")
    return {"name": _str(args, "name", NAME_RE), "script": script, "sha256": _str(args, "sha256", SHA_RE)}


def _linux_run(args: dict) -> dict:
    _only(args, "name", "timeout_s")
    return {"name": _str(args, "name", NAME_RE), "timeout_s": _int(args, "timeout_s", 1, 600, 60)}


def _linux_stop(args: dict) -> dict:
    _only(args, "name")
    return {"name": _str(args, "name", NAME_RE)}


ALLOWLIST = {
    "status": _no_args,
    "mcu.load": _mcu_load,
    "mcu.log": _mcu_log,
    "mcu.recover": _no_args,
    "mcu.lock": _no_args,
    "mcu.unlock": _no_args,
    "mcu.ping": _mcu_ping,
    "mailbox.send": _mailbox_send,
    "linux.put": _linux_put,
    "linux.run": _linux_run,
    "linux.stop": _linux_stop,
    "hardware.inspect": _no_args,
}


# ---- hub client ---------------------------------------------------------------------------------

class HubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Hub:
    def __init__(self, url: str, secret: str):
        self.url = url
        self.headers = {"Authorization": f"Device {secret}", "User-Agent": f"beitlab-link/{VERSION}"}
        self.ssl = ssl.create_default_context()

    def request(self, method: str, path: str, body: object = None, *, timeout: float = 15,
                raw: bool = False):
        data = None
        headers = dict(self.headers)
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self.ssl) as resp:
                payload = resp.read(MAX_ELF_BYTES + 1)
                if resp.status == 204:
                    return None
                return payload if raw else json.loads(payload or b"null")
        except urllib.error.HTTPError as exc:
            raise HubError(exc.code, f"HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise HubError(0, str(getattr(exc, "reason", exc))) from None


class Output:
    """Buffers job output and posts it in chunks (every 0.5 s or 4 KiB)."""

    def __init__(self, hub: Hub, job_id: str):
        self.hub, self.job_id = hub, job_id
        self.buf: dict[str, list[str]] = {}
        self.size = 0
        self.last = time.monotonic()
        self.lock = threading.Lock()

    def write(self, stream: str, text: str) -> None:
        with self.lock:
            self.buf.setdefault(stream, []).append(text)
            self.size += len(text)
            due = self.size >= 4096 or time.monotonic() - self.last >= 0.5
        if due:
            self.flush()

    def flush(self) -> None:
        with self.lock:
            pending, self.buf, self.size, self.last = self.buf, {}, 0, time.monotonic()
        for stream, parts in pending.items():
            text = "".join(parts)
            for i in range(0, len(text), MAX_CHUNK):
                try:
                    self.hub.request("POST", f"/v1/device/jobs/{self.job_id}/output",
                                     {"stream": stream, "text": text[i:i + MAX_CHUNK]})
                except HubError as exc:
                    log.warning("output for job %s lost: %s", self.job_id, exc)
                    return


# ---- the link -----------------------------------------------------------------------------------

class Link:
    def __init__(self, cfg: Config, hub: Hub):
        self.cfg, self.hub = cfg, hub
        self.stop = threading.Event()
        self.mcu = threading.Lock()  # one C906 operation at a time
        self.runs: dict[str, subprocess.Popen] = {}
        self.runs_lock = threading.Lock()
        self.active = threading.BoundedSemaphore(MAX_JOBS)
        self.state = {"state": "connecting", "url": cfg.url(), "claimed": "unknown", "error": ""}

    # -- status file --
    def set_state(self, **kw: str) -> None:
        self.state.update({k: str(v) for k, v in kw.items()})
        self.state["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        write_status(self.cfg.status_file, self.state)

    # -- commands --
    def run_cmd(self, argv: list[str], *, stdin: bytes | None = None, timeout: float = 30) -> tuple[int, str]:
        try:
            p = subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout, check=False,
                               env=minimal_env())
        except FileNotFoundError:
            return 127, f"{argv[0]}: not found\n"
        except subprocess.TimeoutExpired:
            return 124, f"{argv[0]}: timed out after {timeout:.0f} s\n"
        text = (p.stdout + p.stderr).decode("utf-8", "replace")
        return p.returncode, text

    def rtos(self, *args: str, stdin: bytes | None = None, timeout: float = 30) -> tuple[int, str]:
        return self.run_cmd(self.cfg.rtos_control + list(args), stdin=stdin, timeout=timeout)

    def rtos_status(self) -> dict[str, str]:
        rc, text = self.rtos("status", timeout=10)
        if rc != 0:
            return {"state": "unknown"}
        out = {}
        for line in text.splitlines():
            key, sep, value = line.partition("=")
            if sep and re.match(r"^[a-z_]{1,32}$", key):
                out[key] = value.strip()[:128]
        return out

    def board_status(self) -> dict | None:
        if self.cfg.hardware() != "robotics_board_rev011":
            return None
        try:
            data = json.loads(self.cfg.board_status_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    # -- heartbeat --
    def heartbeat_body(self) -> dict:
        body = {
            "hostname": self.cfg.hostname,
            "release": self.cfg.release(),
            "hardware": self.cfg.hardware(),
            "boot_id": self.cfg.read(Path("/proc/sys/kernel/random/boot_id"))[:64],
            "uptime_s": proc_uptime(),
            "link_version": VERSION,
            "rtos": self.rtos_status(),
        }
        status = self.board_status()
        if status is not None:
            body["board_status"] = status
        return body

    def heartbeat(self) -> bool:
        try:
            r = self.hub.request("POST", "/v1/device/heartbeat", self.heartbeat_body())
        except HubError as exc:
            if exc.status == 401:
                self.set_state(state="unauthorized", error="the Studio does not accept this board "
                               "(revoked, or a new card is needed)")
            else:
                self.set_state(state="offline", error=str(exc))
            return False
        self.set_state(state="online", claimed="yes" if r and r.get("claimed") else "no", error="")
        return True

    def heartbeat_loop(self) -> None:
        while not self.stop.wait(self.cfg.heartbeat_s):
            self.heartbeat()

    # -- jobs --
    def poll_loop(self) -> None:
        backoff = 1.0
        while not self.stop.is_set():
            try:
                job = self.hub.request("GET", f"/v1/device/jobs?wait={self.cfg.poll_wait_s}",
                                       timeout=self.cfg.poll_wait_s + 10)
                backoff = 1.0
            except HubError as exc:
                delay = 60.0 if exc.status == 401 else backoff
                log.info("poll failed (%s); retrying in %.0f s", exc, delay)
                if exc.status == 401:
                    self.heartbeat()
                self.stop.wait(delay * random.uniform(0.8, 1.2))
                backoff = min(backoff * 2, 60.0)
                continue
            if job is not None:
                self.dispatch(job)

    def dispatch(self, job: object) -> None:
        job_id = job.get("id") if isinstance(job, dict) else None
        if not isinstance(job_id, str) or not re.match(r"^[A-Za-z0-9_-]{1,64}$", job_id):
            log.warning("refused a job without a valid id")
            return
        try:
            kind, args = check_job(job)
        except Refused as exc:
            log.warning("refused job %s: %s", job_id, exc)
            self.result(job_id, {"ok": False, "error": f"refused by the board: {exc}"})
            return
        if not self.active.acquire(blocking=False):
            self.result(job_id, {"ok": False, "error": "the board is busy; try again"})
            return
        threading.Thread(target=self.run_job, args=(job_id, kind, args), daemon=True,
                         name=f"job-{job_id}").start()

    def result(self, job_id: str, res: dict) -> None:
        try:
            self.hub.request("POST", f"/v1/device/jobs/{job_id}/result", res)
        except HubError as exc:
            log.warning("result for job %s lost: %s", job_id, exc)

    def run_job(self, job_id: str, kind: str, args: dict) -> None:
        out = Output(self.hub, job_id)
        try:
            log.info("job %s: %s", job_id, kind)
            res = getattr(self, "job_" + kind.replace(".", "_"))(job_id, args, out)
        except Exception as exc:  # a failing job must never stop the link
            log.exception("job %s failed", job_id)
            res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:2000]}
        finally:
            self.active.release()
        out.flush()
        self.result(job_id, res)

    @staticmethod
    def _rc(rc: int, text: str, out: Output) -> dict:
        if text:
            out.write("stdout", text)
        return {"ok": rc == 0, "exit_code": rc}

    def job_status(self, job_id, args, out):
        return {"ok": True, "data": {
            "rtos": self.rtos_status(), "release": self.cfg.release(), "hardware": self.cfg.hardware(),
            "uptime_s": proc_uptime(), "board_status": self.board_status()}}

    def job_mcu_load(self, job_id, args, out):
        elf = self.hub.request("GET", f"/v1/device/jobs/{job_id}/elf", raw=True, timeout=60)
        if not isinstance(elf, bytes) or len(elf) != args["size"]:
            return {"ok": False, "error": "the program file has the wrong size"}
        if hashlib.sha256(elf).hexdigest() != args["sha256"]:
            return {"ok": False, "error": "the program file is damaged (sha256 mismatch)"}
        with self.mcu:
            rc, text = self.rtos("load", stdin=elf, timeout=60)
        if rc == 0:
            save_private(self.cfg.state_dir / "last.elf", elf)
        return self._rc(rc, text, out)

    def job_mcu_log(self, job_id, args, out):
        argv = self.cfg.rtos_control + ["log"] + (["-f"] if args["follow"] else [])
        rc = stream_process(argv, args["seconds"], out, self.stop)
        return {"ok": rc in (0, -signal.SIGTERM, -signal.SIGKILL), "exit_code": rc}

    def job_mcu_recover(self, job_id, args, out):
        with self.mcu:
            return self._rc(*self.rtos("recover"), out)

    def job_mcu_lock(self, job_id, args, out):
        with self.mcu:
            return self._rc(*self.rtos("lock"), out)

    def job_mcu_unlock(self, job_id, args, out):
        with self.mcu:
            return self._rc(*self.rtos("unlock"), out)

    def job_mcu_ping(self, job_id, args, out):
        return self._rc(*self.rtos("ping", str(args["nonce"]), timeout=10), out)

    def job_mailbox_send(self, job_id, args, out):
        rc, text = self.run_cmd(self.cfg.bl_send + [hex(args["id"]), str(args["value"])], timeout=10)
        res = self._rc(rc, text, out)
        if rc == 0 and text.strip().isdigit():
            res["data"] = {"reply": int(text.strip())}
        return res

    def job_linux_put(self, job_id, args, out):
        data = args["script"].encode("utf-8")
        if len(data) > MAX_SCRIPT_BYTES:
            return {"ok": False, "error": "the script is larger than 256 KiB"}
        if hashlib.sha256(data).hexdigest() != args["sha256"]:
            return {"ok": False, "error": "the script is damaged (sha256 mismatch)"}
        save_private(self.cfg.studio_dir / f"{args['name']}.py", data)
        out.write("stdout", f"saved ~/studio/{args['name']}.py ({len(data)} bytes)\n")
        return {"ok": True}

    def job_linux_run(self, job_id, args, out):
        name = args["name"]
        script = self.cfg.studio_dir / f"{name}.py"
        if not script.is_file():
            return {"ok": False, "error": f"~/studio/{name}.py does not exist"}
        with self.runs_lock:
            if name in self.runs and self.runs[name].poll() is None:
                return {"ok": False, "error": f"{name} is already running; stop it first"}
        argv = [self.cfg.python, "-u", str(script)]
        rc = stream_process(argv, args["timeout_s"], out, self.stop, cwd=self.cfg.studio_dir,
                            register=lambda p: self._register(name, p))
        with self.runs_lock:
            self.runs.pop(name, None)
        if rc in (-signal.SIGTERM, -signal.SIGKILL):
            out.write("stderr", f"\n{name} was stopped\n")
        return {"ok": rc == 0, "exit_code": rc}

    def _register(self, name: str, proc: subprocess.Popen) -> None:
        with self.runs_lock:
            self.runs[name] = proc

    def job_linux_stop(self, job_id, args, out):
        with self.runs_lock:
            proc = self.runs.get(args["name"])
        if proc is None or proc.poll() is not None:
            out.write("stdout", f"{args['name']} is not running\n")
            return {"ok": True}
        terminate(proc)
        out.write("stdout", f"stopped {args['name']}\n")
        return {"ok": True}

    def job_hardware_inspect(self, job_id, args, out):
        if self.cfg.hardware() != "robotics_board_rev011":
            return {"ok": False, "error": "board inspection needs the Robotics Board image"}
        last = self.cfg.state_dir / "last.elf"
        with self.mcu:
            rc, text = self.rtos("recover")  # the starter on this image is the board inspector
            out.write("stdout", text)
            if rc != 0:
                return {"ok": False, "exit_code": rc, "error": "could not start the board inspector"}
            time.sleep(1.0)
            rc_s, text_s = self.run_cmd(self.cfg.board_status_cmd, timeout=30)
            if text_s:
                out.write("stdout", text_s)
            if last.is_file():
                rc_l, text_l = self.rtos("load", stdin=last.read_bytes(), timeout=60)
                out.write("stdout", text_l or f"reloaded your program (exit {rc_l})\n")
            else:
                rc_l = 0
                out.write("stdout", "no earlier program to reload; the inspector keeps running\n")
        status = self.board_status()
        ok = rc_s == 0 and status is not None and rc_l == 0
        res = {"ok": ok, "data": {"board_status": status}}
        if not ok:
            res["error"] = "inspection failed" if rc_s != 0 or status is None else "could not reload your program"
        return res

    # -- main --
    def run(self) -> int:
        self.set_state(state="connecting")
        while not self.stop.is_set() and not self.heartbeat():
            self.stop.wait(min(60.0, 5.0 * random.uniform(0.8, 1.2)))
        threading.Thread(target=self.heartbeat_loop, daemon=True, name="heartbeat").start()
        self.poll_loop()
        with self.runs_lock:
            for proc in self.runs.values():
                terminate(proc)
        self.set_state(state="stopped")
        return 0


# ---- helpers ------------------------------------------------------------------------------------

def minimal_env() -> dict[str, str]:
    return {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": os.environ.get("HOME", "/home/beitlab"),
            "LANG": "C.UTF-8", "PYTHONUNBUFFERED": "1"}


def proc_uptime() -> float:
    try:
        return round(float(Path("/proc/uptime").read_text().split()[0]), 1)
    except (OSError, ValueError, IndexError):
        return 0.0


def save_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def write_status(path: Path, state: dict[str, str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("".join(f"{k}={str(v).replace(chr(10), ' ')}\n" for k, v in state.items()))
        os.replace(tmp, path)
    except OSError:
        pass


def terminate(proc: subprocess.Popen) -> None:
    """TERM the process group, then KILL it after 3 s."""
    for sig, wait in ((signal.SIGTERM, 3), (signal.SIGKILL, 3)):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(wait)
            return
        except subprocess.TimeoutExpired:
            continue


def stream_process(argv, seconds, out: Output, stop: threading.Event, *, cwd=None, register=None) -> int:
    """Run argv (no shell), stream stdout/stderr to `out`, stop it after `seconds`."""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, cwd=cwd, env=minimal_env(), start_new_session=True)
    except OSError as exc:
        out.write("stderr", f"{argv[0]}: {exc}\n")
        return 127
    if register:
        register(proc)

    def pump(pipe, stream):
        for line in iter(pipe.readline, b""):
            out.write(stream, line.decode("utf-8", "replace"))
        pipe.close()

    readers = [threading.Thread(target=pump, args=(proc.stdout, "stdout"), daemon=True),
               threading.Thread(target=pump, args=(proc.stderr, "stderr"), daemon=True)]
    for t in readers:
        t.start()
    deadline = time.monotonic() + seconds
    timed_out = False
    while proc.poll() is None:
        if stop.is_set() or time.monotonic() >= deadline:
            timed_out = time.monotonic() >= deadline
            terminate(proc)
            break
        time.sleep(0.1)
    for t in readers:
        t.join(timeout=2)
    if timed_out:
        out.write("stderr", f"\nstopped after {seconds} s\n")
    return proc.wait()


# ---- entry points ---------------------------------------------------------------------------------

HARDWARE_LABEL = {"duos_standalone": "Milk-V Duo S (standalone image)",
                  "robotics_board_rev011": "Beitlab Robotics Board rev 0.11 image"}


def card(cfg: Config) -> str:
    secret = cfg.secret()
    code = claim_code(secret) if SHA_RE.match(secret) else "(no device secret)"
    return (f"Hostname:    {cfg.hostname}\nBoard type:  {HARDWARE_LABEL.get(cfg.hardware(), 'unknown')}\n"
            f"Claim code:  {code}\nStudio:      {cfg.url() or '(not set)'}\n")


def motd(cfg: Config) -> str:
    state = {}
    for line in cfg.read(cfg.status_file).splitlines():
        k, _, v = line.partition("=")
        state[k] = v
    if not cfg.url():
        return "Studio link: off (no Studio address on this card)"
    return f"Studio link: {state.get('state', 'not running')} ({cfg.url()})"


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    cfg = Config()
    if argv[1:] == ["--card"]:
        sys.stdout.write(card(cfg))
        return 0
    if argv[1:] == ["--motd"]:
        print(motd(cfg))
        return 0
    if argv[1:] == ["--claim-code"]:
        secret = cfg.secret()
        if not SHA_RE.match(secret):
            print("beitlab-link: no device secret", file=sys.stderr)
            return 1
        print(claim_code(secret))
        return 0
    if argv[1:] == ["--version"]:
        print(VERSION)
        return 0
    if argv[1:]:
        print(__doc__, file=sys.stderr)
        return 2
    url, secret = cfg.url(), cfg.secret()
    if not url:
        log.info("no Studio address set; the link stays off")
        write_status(cfg.status_file, {"state": "disabled"})
        return 0
    if not valid_url(url):
        log.error("Studio address %r refused: use https://, or http:// on a private network", url)
        write_status(cfg.status_file, {"state": "error", "error": "bad Studio address"})
        return 1
    if not SHA_RE.match(secret):
        log.error("no device secret (%s); flash the card again with the imager", cfg.secret_file)
        write_status(cfg.status_file, {"state": "error", "error": "no device secret"})
        return 1
    link = Link(cfg, Hub(url, secret))
    signal.signal(signal.SIGTERM, lambda *_: link.stop.set())
    signal.signal(signal.SIGINT, lambda *_: link.stop.set())
    log.info("beitlab-link %s: %s as %s (%s)", VERSION, url, cfg.hostname, cfg.hardware())
    return link.run()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
