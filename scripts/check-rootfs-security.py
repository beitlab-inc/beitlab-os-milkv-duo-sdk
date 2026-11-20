#!/usr/bin/env python3
"""Fail image publication if regular-user/SSH/sudo defaults were lost."""
import sys
import tarfile


def check(path):
    with tarfile.open(path, "r:xz") as archive:
        files = {m.name.removeprefix("./"): m for m in archive.getmembers()}

        def text(name):
            return archive.extractfile(files[name]).read().decode()

        def rows(name):
            return {line.split(":")[0]: line.split(":") for line in text(name).splitlines()}

        assert rows("etc/passwd")["beitlab"][2:4] == ["1000", "1000"]
        assert rows("etc/passwd")["beitlab"][5:] == ["/home/beitlab", "/bin/sh"]
        shadow = rows("etc/shadow")
        assert shadow["root"][1].startswith(("!", "*")), "root password must be locked"
        assert shadow["beitlab"][1].startswith(("!", "*")), "release must have no shared user password"
        home = files["home/beitlab"]
        assert home.uid == 1000 and home.gid == 1000 and home.mode & 0o777 == 0o700
        sudo = files["usr/bin/sudo"]
        assert sudo.uid == 0 and sudo.mode & 0o4777 == 0o4755
        policy = files["etc/sudoers"]
        assert policy.uid == 0 and policy.mode & 0o777 == 0o440
        assert "NOPASSWD: /usr/libexec/playground/rtos-control" in text("etc/sudoers")
        assert "NOPASSWD: ALL" not in text("etc/sudoers")
        config = text("etc/ssh/sshd_config")
        for directive in ("PermitRootLogin no", "PasswordAuthentication no", "PermitEmptyPasswords no",
                          "KbdInteractiveAuthentication no", "AllowGroups beitlab", "MaxAuthTries 3"):
            assert directive in config, directive
        assert "40-beitlab-user.conf" not in "\n".join(files), "release has personal SSH overrides"
        assert not any(name.startswith("etc/ssh/ssh_host_") and name.endswith("_key") for name in files), "release contains shared SSH host private keys"
        for name in ("usr/libexec/playground/rtos-control", "usr/libexec/playground/import-user",
                     "usr/libexec/playground/install-keys", "usr/bin/beitlab-user-setup"):
            mode = files[name]
            assert mode.uid == 0 and mode.mode & 0o022 == 0 and mode.mode & 0o111
        for unit in ("beitlab-user", "beitlab-user-setup"):
            assert files[f"etc/systemd/system/multi-user.target.wants/{unit}.service"].issym()
        assert "Requires=beitlab-user.service playground-keys.service" in text("etc/systemd/system/sshd.service.d/10-user.conf")
        assert "chown -R beitlab:beitlab" in text("usr/libexec/playground/install-keys")
        assert "umask=0077" in text("etc/fstab")
        assert "boot/playground/user-supported" in files
        assert "boot/playground/user.conf" not in files
        assert files["usr/sbin/chpasswd"].issym() or files["usr/sbin/chpasswd"].mode & 0o111, "password importer needs chpasswd"
        assert "openssl passwd -6 -stdin" in text("usr/bin/beitlab-user-setup")
        assert "login -f" not in text("usr/local/bin/autologin"), "legacy SDK autologin bypass remains"
    print("ok: regular UID 1000 user, private home, locked root/no default password, SSH key defaults, restricted sudo")


if __name__ == "__main__":
    try:
        check(sys.argv[1])
    except (AssertionError, KeyError) as error:
        sys.exit(f"security check failed: {error}")
