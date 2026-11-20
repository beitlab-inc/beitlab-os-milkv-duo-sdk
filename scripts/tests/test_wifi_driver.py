#!/usr/bin/env python3
"""Exercise the boot loader with isolated module and net-device state."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("busybox"), "needs BusyBox")
class DriverTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("modules", "net", "ko", "bin"):
            (self.root / name).mkdir()
        self.calls = self.root / "calls"
        script = (REPO / "image/overlay/usr/libexec/playground/load-wifi").read_text()
        for old, new in (("/sys/module", self.root / "modules"),
                         ("/sys/class/net", self.root / "net"),
                         ("/mnt/system/ko", self.root / "ko")):
            script = script.replace(old, str(new))
        script = script.replace("sleep ", str(self.root / "bin/sleep") + " ")
        script = script.replace("insmod ", str(self.root / "bin/insmod") + " ")
        self.script = self.root / "load-wifi"
        self.script.write_text(script)
        insmod = self.root / "bin/insmod"
        insmod.write_text('''#!/bin/sh
echo "${1##*/}" >> "$WIFI_DRIVER_ROOT/calls"
[ "${WIFI_DRIVER_FAIL:-}" != 1 ] || exit 1
case "$1" in *aic8800_fdrv.ko) mkdir -p "$WIFI_DRIVER_ROOT/net/wlan0" ;; esac
''')
        insmod.chmod(0o755)
        sleep = self.root / "bin/sleep"
        sleep.write_text("#!/bin/sh\nexit 0\n")
        sleep.chmod(0o755)

    def run_loader(self, fail=False):
        env = dict(os.environ, WIFI_DRIVER_ROOT=str(self.root), WIFI_DRIVER_FAIL=str(int(fail)))
        return subprocess.run(["busybox", "sh", str(self.script)], env=env, capture_output=True, text=True)

    def test_automatic_module_order(self):
        result = self.run_loader()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls.read_text().splitlines(), ["aic8800_bsp.ko", "aic8800_fdrv.ko"])

    def test_loaded_drivers_are_not_loaded_again(self):
        for name in ("aic8800_bsp", "aic8800_fdrv"):
            (self.root / "modules" / name).mkdir()
        (self.root / "net/wlan0").mkdir()
        self.assertEqual(self.run_loader().returncode, 0)
        self.assertFalse(self.calls.exists())

    def test_driver_load_failure_is_reported(self):
        self.assertNotEqual(self.run_loader(fail=True).returncode, 0)
        self.assertEqual(self.calls.read_text().splitlines(), ["aic8800_bsp.ko"])

    def test_missing_wireless_hardware_is_reported(self):
        for name in ("aic8800_bsp", "aic8800_fdrv"):
            (self.root / "modules" / name).mkdir()
        result = self.run_loader()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("wlan0 did not appear", result.stderr)


if __name__ == "__main__":
    unittest.main()
