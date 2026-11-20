#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Upload a C906L ELF to a Duo S running beitlab Robotics MilkV duo OS.

  python3 upload.py <firmware.elf>

1. ssh to the board (PIO_DUO_HOST, default beitlab-milkv-duo.local; user
   PIO_DUO_USER, default beitlab; password PIO_DUO_PASS, optional)
2. copy the ELF to a unique file in the regular user home
3. stream the ELF to the restricted sudo helper
4. print what the agent printed and exit with its exit code:
     0 loaded and running   2 not a valid C906L ELF (nothing changed)
     3 board locked         4 did not start (the starter was restarted)
"""
import os
import sys
import secrets
import shlex

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duo_ssh  # noqa: E402  pylint: disable=wrong-import-position

REMOTE = ".playground-upload-" + secrets.token_hex(12) + ".elf"


def main(argv):
    if len(argv) != 2 or not os.path.isfile(argv[1]):
        sys.stderr.write("usage: upload.py <firmware.elf>\n")
        return 1
    elf = argv[1]
    try:
        s = duo_ssh.Session()
    except Exception as exc:  # paramiko connection errors
        duo_ssh.info("cannot connect to %s: %s" % (duo_ssh.TARGET, exc))
        return 255
    try:
        rc = s.run("true")
        if rc != 0:
            duo_ssh.info("ssh to %s failed (exit %d). Is the board on Ethernet and booted? "
                         "Try: ping %s" % (duo_ssh.TARGET, rc, duo_ssh.HOST))
            duo_ssh.info("After re-flashing the card, remove the old host key: "
                         "ssh-keygen -R %s" % duo_ssh.HOST)
            return rc
        duo_ssh.info("copying %s (%d bytes) to %s:%s"
                     % (os.path.basename(elf), os.path.getsize(elf), duo_ssh.HOST, REMOTE))
        rc = s.put(elf, REMOTE)
        if rc != 0:
            duo_ssh.info("copy failed (exit %d)" % rc)
            return rc
        quoted = shlex.quote(REMOTE)
        load = duo_ssh.agent_command("load", REMOTE) if duo_ssh.USER == "root" else duo_ssh.agent_command("load") + " < " + quoted
        rc = s.run("%s; rc=$?; rm -f -- %s; exit $rc" % (load, quoted))
    finally:
        s.close()
    if rc == 3:
        print("The board is locked. Open the E-stop, then run:\n"
              "  ssh %s %s" % (duo_ssh.TARGET, duo_ssh.agent_command("unlock")))
    elif rc == 2:
        print("The board rejected the file; the previous program is still running.")
    elif rc == 4:
        print("The program did not start; the board is running the starter firmware.")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
