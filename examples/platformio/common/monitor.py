#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Stream the C906L log from a Duo S running beitlab Robotics MilkV duo OS.

  python3 monitor.py           (or: pio run -t rtmonitor)

Runs `ssh beitlab@<host> rtos-agent log -f` and keeps the stream open. The
agent prints the C906 dump ring once, then every new line. Same
PIO_DUO_HOST / PIO_DUO_USER / PIO_DUO_PASS settings as upload.py.

Ctrl-C closes the SSH session and exits. Only the log viewer stops: the
remote `rtos-agent log -f` is hung up with the session, and the program on
the C906 keeps running. Uploading from another terminal while the monitor
is open is fine; the monitor prints "--- C906 program restarted ---".
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duo_ssh  # noqa: E402  pylint: disable=wrong-import-position


def main():
    try:
        s = duo_ssh.Session()
    except Exception as exc:  # paramiko connection errors
        duo_ssh.info("cannot connect to %s: %s" % (duo_ssh.TARGET, exc))
        return 255
    duo_ssh.info("C906 log from %s (Ctrl-C to stop)" % duo_ssh.TARGET)
    try:
        rc = s.stream(duo_ssh.agent_command("log", "-f"))
    finally:
        s.close()
    return 0 if rc == 130 else rc


if __name__ == "__main__":
    sys.exit(main())
