# SPDX-License-Identifier: MIT
"""SSH transport shared by upload.py and monitor.py (PC side only).

Connection settings come from the environment:
  PIO_DUO_HOST  board address          (default beitlab-milkv-duo.local)
  PIO_DUO_USER  login user             (default beitlab)
  PIO_DUO_PASS  password, optional     (no shared default)

Without PIO_DUO_PASS, OpenSSH uses your keys or asks for the password once
(the connection is shared between steps where OpenSSH supports it).
With PIO_DUO_PASS, paramiko is used if installed (pip install paramiko),
else sshpass, else OpenSSH asks.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import shlex

HOST = os.environ.get("PIO_DUO_HOST", "beitlab-milkv-duo.local")
USER = os.environ.get("PIO_DUO_USER", "beitlab")
PASSWORD = os.environ.get("PIO_DUO_PASS") or None
TARGET = "%s@%s" % (USER, HOST)

SSH_OPTS = ["-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes",
            "-o", "ServerAliveInterval=5"]


def info(msg):
    sys.stderr.write("[duo] %s\n" % msg)
    sys.stderr.flush()


def _paramiko():
    if not PASSWORD:
        return None
    try:
        import paramiko  # pylint: disable=import-outside-toplevel
    except ImportError:
        return None
    return paramiko


class Session:
    """run(cmd) -> exit code, put(local, remote), stream(cmd) -> exit code."""

    def __init__(self):
        self.pm = _paramiko()
        self.client = None
        self.ctl_dir = None
        self.prefix = []
        if self.pm:
            self.client = self.pm.SSHClient()
            self.client.load_system_host_keys()
            self.client.set_missing_host_key_policy(self.pm.RejectPolicy())
            info("connecting to %s (paramiko)" % TARGET)
            self.client.connect(HOST, username=USER, password=PASSWORD, timeout=10,
                                allow_agent=True, look_for_keys=True)
            return
        if PASSWORD:
            if shutil.which("sshpass"):
                os.environ["SSHPASS"] = PASSWORD
                self.prefix = ["sshpass", "-e"]
            else:
                info("PIO_DUO_PASS is set but neither paramiko nor sshpass is installed; "
                     "ssh will ask for the password")
        self.opts = list(SSH_OPTS)
        if os.name != "nt":  # OpenSSH on Windows has no connection sharing
            self.ctl_dir = tempfile.mkdtemp(prefix="duo-ssh-")
            sock = os.path.join(self.ctl_dir, "ctl")
            self.opts += ["-o", "ControlMaster=auto", "-o", "ControlPath=" + sock,
                          "-o", "ControlPersist=30"]

    def close(self):
        if self.client:
            self.client.close()
        if self.ctl_dir:
            subprocess.call(self.prefix + ["ssh"] + self.opts + ["-O", "exit", TARGET],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            shutil.rmtree(self.ctl_dir, ignore_errors=True)

    def run(self, command):
        """Run a remote command, copy its output through, return its exit code."""
        if self.client:
            _in, out, err = self.client.exec_command(command)
            for line in out:
                sys.stdout.write(line)
            for line in err:
                sys.stderr.write(line)
            return out.channel.recv_exit_status()
        return subprocess.call(self.prefix + ["ssh"] + self.opts + [TARGET, command])

    def put(self, local, remote):
        if self.client:
            sftp = self.client.open_sftp()
            try:
                sftp.put(local, remote)
            finally:
                sftp.close()
            return 0
        return subprocess.call(self.prefix + ["scp", "-q"] + self.opts +
                               [local, "%s:%s" % (TARGET, remote)])

    def stream(self, command):
        """Run a long-lived command with a remote tty (so the remote side is
        hung up when we leave) until it ends or Ctrl-C."""
        if self.client:
            chan = self.client.get_transport().open_session()
            chan.get_pty()
            chan.exec_command(command)
            try:
                while True:
                    data = chan.recv(4096)
                    if not data:
                        break
                    sys.stdout.write(data.decode("utf-8", "replace"))
                    sys.stdout.flush()
            except KeyboardInterrupt:
                chan.close()
                return 130
            return chan.recv_exit_status()
        proc = subprocess.Popen(self.prefix + ["ssh", "-tt"] + self.opts + [TARGET, command])
        try:
            return proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            proc.wait()
            return 130

    def admin(self, command):
        """Interactive administration uses OpenSSH's tty for the sudo prompt."""
        return subprocess.call(self.prefix + ["ssh", "-tt"] + SSH_OPTS + [TARGET, command])


def main(argv):
    """python3 duo_ssh.py <command...>: run one command on the board with the
    same connection settings as upload.py, exit with its exit code."""
    if len(argv) < 2:
        sys.stderr.write("usage: duo_ssh.py <command> [args...]\n")
        return 2
    try:
        s = Session()
    except Exception as exc:  # paramiko connection errors, as in upload.py
        info("cannot connect to %s: %s" % (TARGET, exc))
        return 255
    try:
        args = argv[1:]
        if args[0] == "host_ping" and USER != "root":
            return s.run(agent_command("ping", *args[1:]))
        return s.run(shlex.join(args))
    finally:
        s.close()


def agent_command(*args):
    """Only an explicitly selected legacy root login uses the old interface."""
    prefix = ["rtos-agent"] if USER == "root" else ["sudo", "-n", "/usr/libexec/playground/rtos-control"]
    return shlex.join(prefix + list(args))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
