#!/usr/bin/env python3
"""Check the shipped tarball, including systemd's enabled service links."""
import sys
import tarfile


def check(path, version):
    with tarfile.open(path, "r:xz") as archive:
        files = {m.name.removeprefix("./"): m for m in archive.getmembers()}

        def require(name):
            assert name in files, f"rootfs missing /{name}"
            return files[name]

        def content(name):
            return archive.extractfile(require(name)).read().decode()

        for name in ("aic8800_bsp", "aic8800_fdrv"):
            require(f"mnt/system/ko/{name}.ko")
        bsp = archive.extractfile(require("mnt/system/ko/aic8800_bsp.ko")).read()
        assert b"/mnt/system/firmware/aic8800\0" in bsp, "AIC8800 firmware path differs from shipped payload"
        # The entire vendor firmware directory is copied by the SDK; check
        # the Duo S D80 payload as well as the driver-configured path.
        for name in ("fmacfw_8800d80_u02.bin", "fw_adid_8800d80_u02.bin",
                     "fw_patch_8800d80_u02.bin", "fw_patch_table_8800d80_u02.bin"):
            require(f"mnt/system/firmware/aic8800/{name}")
        for name in ("wpa_supplicant", "wpa_cli", "wpa_passphrase"):
            assert require(f"usr/sbin/{name}").mode & 0o111
        for name in ("usr/bin/beitlab-wifi-setup", "usr/libexec/playground/load-wifi",
                     "usr/libexec/playground/import-wifi",
                     "usr/libexec/playground/import-hostname", "usr/libexec/playground/install-keys",
                     "usr/bin/rtos-agent", "usr/libexec/playground/load-modules"):
            assert require(name).mode & 0o111, f"/{name} is not executable"
        config = "etc/wpa_supplicant/beitlab-wlan0.conf"
        assert require(config).mode & 0o077 == 0, "Wi-Fi profile must be private"
        assert require(config).uid == 0, "Wi-Fi profile must belong to root"
        assert "network={" not in content(config), "image contains Wi-Fi credentials"
        require("boot/playground/wifi-supported")
        require("boot/playground/setup-supported")
        for unit in ("beitlab-wifi", "beitlab-wifi-import", "beitlab-wpa", "beitlab-network-setup",
                     "beitlab-hostname", "playground-keys", "rtos-agent", "dhcpcd", "sshd", "avahi-daemon"):
            require(f"usr/lib/systemd/system/{unit}.service")
            link = require(f"etc/systemd/system/multi-user.target.wants/{unit}.service")
            assert link.issym() and link.linkname.endswith(f"/{unit}.service"), unit
        require("etc/systemd/system/sysinit.target.wants/playground-modules.service")
        agent = content("usr/lib/systemd/system/rtos-agent.service")
        assert "ExecStart=/usr/bin/rtos-agent boot" in agent
        assert "RemainAfterExit=yes" in agent
        assert "network" not in agent.lower(), "agent must boot independently of networking"
        setup = content("usr/lib/systemd/system/beitlab-network-setup.service")
        assert "After=beitlab-wpa.service dhcpcd.service rtos-agent.service" in setup
        assert "Before=serial-getty@ttyS0.service" in setup
        assert "TTYPath=/dev/ttyS0" in setup
        importer = content("usr/lib/systemd/system/beitlab-wifi-import.service")
        assert "RequiresMountsFor=/boot" in importer
        assert "Before=beitlab-wpa.service" in importer
        hostname = content("usr/lib/systemd/system/beitlab-hostname.service")
        assert "RequiresMountsFor=/boot" in hostname
        assert "Before=dhcpcd.service sshd.service avahi-daemon.service beitlab-wpa.service" in hostname
        assert "Before=sshd.service" in content("usr/lib/systemd/system/playground-keys.service")
        wpa = content("usr/lib/systemd/system/beitlab-wpa.service")
        assert "Requires=beitlab-wifi.service" in wpa
        assert "Wants=beitlab-wifi-import.service" in wpa
        assert "After=beitlab-wifi.service beitlab-wifi-import.service" in wpa
        assert "-c /etc/wpa_supplicant/beitlab-wlan0.conf" in wpa
        assert "ExecStart=/usr/libexec/playground/load-wifi" in content("usr/lib/systemd/system/beitlab-wifi.service")
        assert "nohook wpa_supplicant" in content("etc/dhcpcd.conf")
        assert "allowinterfaces end0 eth0 wlan0" in content("etc/dhcpcd.conf")
        assert content("etc/hostname").strip() == "beitlab-milkv-duo"
        assert f'PRETTY_NAME="beitlab Robotics MilkV duo OS {version}"' in content("usr/lib/os-release")
        assert f"beitlab Robotics MilkV duo OS {version}" in content("etc/issue")
        assert "beitlab-milkv-duo.local" in content("etc/issue")
    print("ok: AIC8800 drivers/firmware, boot Wi-Fi import ordering, private profile, console setup, OS identity")
    print("ok: rtos-agent enabled from boot and starts before the network wizard")


if __name__ == "__main__":
    try:
        check(*sys.argv[1:])
    except (AssertionError, KeyError) as error:
        sys.exit(f"rootfs check failed: {error}")
