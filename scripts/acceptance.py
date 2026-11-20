#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run PROMPT.md acceptance tests 1-7 against a booted Duo S, over SSH.

PC side only (needs paramiko and PlatformIO Core 6 on PATH). Test 8 is the
dtb inspection in scripts/build-image.sh and is not repeated here.

  PIO_DUO_HOST=beitlab-milkv-duo.local scripts/acceptance.py

Every command and its output is printed and also written to
out/acceptance-<UTC time>.log, so the log can go into docs/sdk-notes.md.
The serial-console half of test 2 (RT: lines on UART0) cannot be seen over
SSH and is reported as "manual".

Uploads and recover restart the C906. With robot firmware and the carrier
powered, lock the board or open the E-stop first; this script refuses to
start if the board is locked.
"""
import datetime
import os
import re
import subprocess
import sys
import time

try:
    import paramiko
except ImportError:
    sys.exit("acceptance.py needs paramiko: pip install paramiko")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(REPO, "examples", "platformio")
HOST = os.environ.get("PIO_DUO_HOST", "beitlab-milkv-duo.local")
USER = os.environ.get("PIO_DUO_USER", "beitlab")
PASSWORD = os.environ.get("PIO_DUO_PASS") or None
PIO = os.environ.get("PIO_BIN", "pio")

stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
WORK = os.environ.get("BEITLAB_WORK_DIR", os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "beitlab-duo-os"))
REPORTS = os.path.join(WORK, "reports")
os.makedirs(REPORTS, exist_ok=True)
LOG = open(os.path.join(REPORTS, "acceptance-%s.log" % stamp), "w")
results = []


def say(text=""):
    print(text)
    LOG.write(text + "\n")
    LOG.flush()


def record(test, ok, why):
    status = {True: "PASS", False: "FAIL", None: "manual"}[ok]
    results.append((test, status, why))
    say("==> test %s: %s (%s)" % (test, status, why))


def lock_file(path):
    out = {}
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class Board:
    def __init__(self):
        self.c = paramiko.SSHClient()
        self.c.load_system_host_keys()
        self.c.set_missing_host_key_policy(paramiko.RejectPolicy())
        self.c.connect(HOST, username=USER, password=PASSWORD, timeout=10)

    def sh(self, cmd, show=True):
        if USER != "root":
            cmd = re.sub(r"rtos-agent load ([^; ]+)", r"sudo -n /usr/libexec/playground/rtos-control load < \1", cmd)
            cmd = cmd.replace("rtos-agent ", "sudo -n /usr/libexec/playground/rtos-control ")
            cmd = cmd.replace("host_ping 7", "sudo -n /usr/libexec/playground/rtos-control ping 7")
        _, o, e = self.c.exec_command(cmd, timeout=60)
        out = (o.read() + e.read()).decode("utf-8", "replace").rstrip()
        rc = o.channel.recv_exit_status()
        if show:
            say("$ " + cmd)
            if out:
                say(out)
            say("[exit %d]" % rc)
        return rc, out

    def kv(self, cmd):
        return dict(l.split("=", 1) for l in self.sh(cmd)[1].splitlines() if "=" in l)

    def ids(self):
        up = float(self.sh("cat /proc/uptime")[1].split()[0])
        boot = self.sh("cat /proc/sys/kernel/random/boot_id")[1].strip()
        return up, boot

    def log_tail(self, n=4):
        return self.sh("rtos-agent log | tail -%d" % n)[1]


def run_local(cmd, cwd):
    env = dict(os.environ, PIO_DUO_HOST=HOST)
    say("$ (cd %s; %s)" % (os.path.relpath(cwd, REPO), " ".join(cmd)))
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    lines = (p.stdout + p.stderr).splitlines()
    # Vendor main.c warnings are noise here; keep the platform's own lines.
    keep = [l for l in lines if l.startswith(("[duo]", "***", "rtosbuild", "host_ping",
                                              "locked", "The board", "  ssh", "=====",
                                              "bad file", "loaded", "error"))]
    for l in keep:
        say(l)
    say("[exit %d]" % p.returncode)
    return p.returncode, "\n".join(lines)


def upload(example):
    return run_local([PIO, "run", "-t", "upload"], os.path.join(EXAMPLES, example))


def main():
    say("acceptance run %s against %s@%s" % (stamp, USER, HOST))
    lock = lock_file(os.path.join(REPO, "sdk.lock"))
    version = open(os.path.join(REPO, "VERSION")).read().strip()

    # Test 1: identity
    say("\n## test 1: image identity")
    p = subprocess.run(["ping", "-c", "3", "-W", "2", HOST], capture_output=True, text=True)
    say("$ ping -c 3 %s" % HOST)
    say("\n".join(p.stdout.strip().splitlines()[-2:]))
    b = Board()
    say("ssh %s@%s: connected" % (USER, HOST))
    rel = b.kv("cat /etc/duo-playground-release")
    act = b.sh("systemctl is-active rtos-agent.service sshd avahi-daemon "
               "playground-modules.service")[1].split()
    b.sh("ip -4 addr; for i in /sys/class/net/*; do n=${i##*/}; "
         "echo \"$n carrier=$(cat $i/carrier 2>/dev/null)\"; done")
    wired = b.sh("for i in /sys/class/net/e*; do [ \"$(cat $i/carrier 2>/dev/null)\" = 1 ] "
                 "&& ip -4 addr show ${i##*/} | grep -q inet && echo ${i##*/}; done", show=False)[1]
    problems = []
    if p.returncode != 0:
        problems.append("ping failed")
    if rel.get("SDK_COMMIT") != lock["sdk_commit"] or rel.get("SDK_TARGET") != lock["target"]:
        problems.append("release file differs from sdk.lock")
    if rel.get("DUO_PLAYGROUND_VERSION") != version:
        problems.append("version %s != VERSION %s" % (rel.get("DUO_PLAYGROUND_VERSION"), version))
    if act != ["active"] * 4:
        problems.append("services: %s" % " ".join(act))
    if problems:
        record(1, False, "; ".join(problems))
    elif not wired:
        record(1, None, "all checks pass, but no wired interface has carrier and an address; "
                        "the Ethernet link is unproven")
    else:
        record(1, True, "ping, ssh, release = sdk.lock, services active, Ethernet on %s" % wired)

    st = b.kv("rtos-agent status")
    if st.get("lock") != "unlocked":
        say("board is locked; unlock it (E-stop open) before running uploads")
        return 1

    # Test 2: core came up with Linux
    say("\n## test 2: core came up with Linux")
    _, j = b.sh("journalctl -b -k -o short-monotonic | grep -E 'Booting fw|is now up' | head -2")
    m = re.findall(r"\[\s*([0-9.]+)\].*is now up", j)
    banner = b.log_tail(6)
    if st.get("state") == "running" and m and float(m[0]) < 30:
        record(2, True, "is now up at %s s; serial RT: lines must be checked on UART0" % m[0])
    else:
        record(2, False, "state=%s, up at %s" % (st.get("state"), m[0] if m else "never"))
    if "playground starter" not in banner:
        say("note: the boot program is the last upload, not the starter "
            "(/lib/firmware/playground.elf is replaced by every load)")

    # Test 4 before 3, while the boot program runs
    say("\n## test 4: bad file")
    rc, out = b.sh("echo hello > /tmp/bad.txt; rtos-agent load /tmp/bad.txt")
    after = b.kv("rtos-agent status")
    b.sh("rm -f /tmp/bad.txt", show=False)
    record(4, rc == 2 and after.get("state") == "running",
           "exit %d, state %s" % (rc, after.get("state")))

    # Test 3: hello upload, Linux stays up
    say("\n## test 3: hello upload")
    up0, boot0 = b.ids()
    rc, _ = upload("hello")
    time.sleep(2)
    up1, boot1 = b.ids()      # same SSH session as before the upload
    st = b.kv("rtos-agent status")
    tail = b.log_tail()
    ok = rc == 0 and st.get("state") == "running" and "playground hello" in tail \
        and boot1 == boot0 and up1 > up0
    record(3, ok, "upload exit %d, state %s, hello banner %s, uptime %.1f -> %.1f s, boot_id %s"
           % (rc, st.get("state"), "seen" if "playground hello" in tail else "missing",
              up0, up1, "same" if boot1 == boot0 else "CHANGED"))

    # Test 5: lock
    say("\n## test 5: lock")
    b.sh("rtos-agent lock")
    hello_elf = os.path.join(EXAMPLES, "hello", ".pio", "build", "hello", "firmware.elf")
    rc_up, _ = run_local([sys.executable, "../common/upload.py", hello_elf],
                         os.path.join(EXAMPLES, "hello"))
    b.sh("rtos-agent unlock")
    rc_ok, _ = upload("hello")
    record(5, rc_up == 3 and rc_ok == 0,
           "locked upload.py exit %d, unlocked pio upload exit %d" % (rc_up, rc_ok))

    # Test 6: recover
    say("\n## test 6: recover")
    rc, _ = b.sh("rtos-agent recover")
    time.sleep(2)
    tail = b.log_tail(3)
    rc_f, _ = b.sh("ls -l /lib/firmware/playground-starter.elf")
    record(6, rc == 0 and "playground starter" in tail and rc_f == 0,
           "recover exit %d, starter banner %s, starter file %s"
           % (rc, "seen" if "playground starter" in tail else "missing",
              "present" if rc_f == 0 else "MISSING"))

    # Test 7: mailbox
    say("\n## test 7: mailbox")
    rc, _ = upload("mailbox_ping")
    time.sleep(2)
    b.log_tail(2)
    rc_b, out_b = b.sh("host_ping 7")
    rc_p, out_p = run_local(["./host_ping.sh", "7"], os.path.join(EXAMPLES, "mailbox_ping"))
    ok = rc == 0 and rc_b == 0 and "reply 8 == 7 + 1, ok" in out_b and rc_p == 0
    record(7, ok, "upload exit %d, board host_ping exit %d, PC host_ping.sh exit %d"
           % (rc, rc_b, rc_p))

    # Leave the board on the starter, and check Linux never restarted.
    say("\n## end")
    b.sh("rtos-agent recover")
    up2, boot2 = b.ids()
    say("boot_id %s for the whole run; uptime %.1f s"
        % ("unchanged" if boot2 == boot0 else "CHANGED", up2))

    say("\n## summary")
    say("| # | result | detail |")
    say("| --- | --- | --- |")
    for t, s, why in sorted(results):
        say("| %d | %s | %s |" % (t, s, why))
    say("\nlog: %s" % LOG.name)
    return 0 if all(s != "FAIL" for _, s, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
