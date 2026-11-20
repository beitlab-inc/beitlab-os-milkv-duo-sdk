#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build bl_send for the Duo S (aarch64 Linux) and install it on the board.

  python3 install.py          (from any folder)

1. Compile bl_send.c with the aarch64 Linux compiler from the SDK
   host-tools (sdk.lock host_tools_aarch64_linux), static, into
   ../.pio/linux/bl_send. Set CC to use another aarch64-linux-gnu-gcc.
2. Copy it to the board as /usr/bin/bl_send, over the same SSH settings
   as upload.py (PIO_DUO_HOST, PIO_DUO_USER, PIO_DUO_PASS).

The file lives on the SD card until the card is re-flashed.
"""
import os
import subprocess
import sys
import secrets
import shlex

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "examples", "platformio", "common"))
import duo_ssh  # noqa: E402  pylint: disable=wrong-import-position

REMOTE = "/usr/bin/bl_send"
STAGE = ".bl_send-" + secrets.token_hex(12)


def sdk_compiler():
    with open(os.path.join(REPO, "sdk.lock")) as f:
        for line in f:
            key, _, value = line.partition("=")
            if key.strip() == "host_tools_aarch64_linux":
                work = os.environ.get("BEITLAB_WORK_DIR", os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "beitlab-duo-os"))
                sdk = os.environ.get("PLAYGROUND_SDK", os.path.join(work, "sdk"))
                return os.path.join(sdk, "host-tools", value.strip(), "bin",
                                    "aarch64-linux-gnu-gcc")
    return None


def main():
    cc = os.environ.get("CC") or sdk_compiler()
    if not cc or not os.path.isfile(cc):
        duo_ssh.info("no aarch64 compiler at %s; set CC=<path to aarch64-linux-gnu-gcc>" % cc)
        return 1
    out = os.path.join(HERE, "..", ".pio", "linux", "bl_send")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    rc = subprocess.call([cc, "-O2", "-Wall", "-static", "-o", out,
                          os.path.join(HERE, "bl_send.c")])
    if rc != 0:
        duo_ssh.info("compile failed (exit %d)" % rc)
        return rc
    duo_ssh.info("built %s (%d bytes)" % (os.path.normpath(out), os.path.getsize(out)))
    try:
        s = duo_ssh.Session()
    except Exception as exc:  # paramiko connection errors, as in upload.py
        duo_ssh.info("cannot connect to %s: %s" % (duo_ssh.TARGET, exc))
        return 255
    try:
        rc = s.put(out, STAGE)
        if rc == 0:
            command = "install -o root -g root -m 0755 %s %s" % (shlex.quote(STAGE), REMOTE)
            if duo_ssh.USER != "root":
                command = "sudo -- " + command
            command += "; rc=$?; rm -f -- %s; exit $rc" % shlex.quote(STAGE)
            rc = s.admin(command) if duo_ssh.USER != "root" else s.run(command)
    finally:
        s.close()
    if rc != 0:
        duo_ssh.info("install failed (exit %d)" % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
