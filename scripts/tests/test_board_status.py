"""beitlab-board-status decoding (PROMPT.md 0.5.0, board inspector).

The register values below are synthetic: computed from the ADS1115 scaling (PGA code 1 = 4.096 V
full scale) and the board_carrier.h divider gains, not recorded from a carrier. Replace them with
recorded values once a carrier has been inspected (docs/sdk-notes.md); until then this is
**not verified on hardware**.
"""
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "image/overlay/usr/bin/beitlab-board-status"
LOADER = importlib.machinery.SourceFileLoader("board_status", str(PATH))
SPEC = importlib.util.spec_from_loader("board_status", LOADER)
BS = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(BS)


def raw(volts_at_rail, gain, fsr=4.096):
    return round(volts_at_rail / gain / fsr * 32768) & 0xFFFF


def fields(found=7, imu=0x0043, vbat=15.2, vservo=6.0, v5=5.32, vmot=15.1, nfault=1, pga=1, mode1=0x11):
    return [1, found, imu, raw(vbat, 11), raw(vservo, 5.7), raw(v5, 5.7), raw(vmot, 11), pga, mode1, nfault]


class Decode(unittest.TestCase):
    def test_healthy_carrier(self):
        r = BS.decode(fields(), "2026-11-23T10:00:00Z")
        self.assertTrue(r["ok"], r["warnings"])
        self.assertEqual(r["carrier"], "found")
        self.assertEqual(r["imu"], {"ok": True, "found": True, "chip_id": "0x0043"})
        self.assertAlmostEqual(r["rails"]["vbat"], 15.2, delta=0.02)
        self.assertAlmostEqual(r["rails"]["v5"], 5.32, delta=0.02)
        self.assertEqual(r["motor_power"], "on")
        self.assertEqual(r["servo_driver"]["mode1"], "0x11")

    def test_bare_duo_s_means_carrier_not_found(self):
        r = BS.decode([1, 0, 0, 0, 0, 0, 0, 0, 0, 0], "t")
        self.assertEqual(r["carrier"], "missing")
        self.assertFalse(r["ok"])
        self.assertEqual(len(r["warnings"]), 1)
        self.assertIn("standalone image", r["warnings"][0])

    def test_low_battery_and_bad_5v_warn(self):
        r = BS.decode(fields(vbat=12.4, vmot=12.3, v5=4.6), "t")
        self.assertFalse(r["ok"])
        self.assertFalse(r["rails"]["ok"])
        text = " ".join(r["warnings"])
        self.assertIn("vbat is 12.40 V", text)
        self.assertIn("v5 is 4.60 V", text)

    def test_estop_open_is_not_a_fault(self):
        r = BS.decode(fields(vmot=0.05), "t")
        self.assertTrue(r["ok"], r["warnings"])
        self.assertEqual(r["motor_power"], "off (E-stop open or not armed)")

    def test_vmot_not_following_the_battery_warns(self):
        r = BS.decode(fields(vmot=9.0), "t")
        self.assertFalse(r["ok"])
        self.assertIn("does not follow the battery", " ".join(r["warnings"]))

    def test_driver_fault_and_missing_parts(self):
        r = BS.decode(fields(nfault=0), "t")
        self.assertFalse(r["ok"])
        self.assertIn("DRV_NFAULT", " ".join(r["warnings"]))
        r = BS.decode(fields(found=1 | 2), "t")
        self.assertFalse(r["servo_driver"]["ok"])
        self.assertIn("PCA9685", " ".join(r["warnings"]))

    def test_wrong_chip_id(self):
        r = BS.decode(fields(imu=0x00D1), "t")
        self.assertFalse(r["imu"]["ok"])
        self.assertIn("0x00d1", " ".join(r["warnings"]))

    def test_negative_reading_and_unknown_format(self):
        self.assertLess(BS.volts(0xFFF0, 1, 11.0), 0)
        with self.assertRaises(ValueError):
            BS.decode([2] + [0] * 9, "t")


FAKE_BL_SEND = """#!{py}
import sys
FIELDS = {fields}
cmd, value = int(sys.argv[1], 16), int(sys.argv[2])
if cmd == 0x10:
    print((5 << 1) | 1)
elif cmd == 0x11 and value < len(FIELDS):
    print(FIELDS[value])
else:
    print("bl_send: refused"); sys.exit(4)
"""


class EndToEnd(unittest.TestCase):
    def test_main_reads_fields_with_bl_send_and_writes_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = Path(tmp) / "bl_send"
            tool.write_text(FAKE_BL_SEND.format(py=sys.executable, fields=fields()))
            tool.chmod(0o755)
            out = Path(tmp) / "run" / "board-status.json"
            saved = dict(os.environ)
            os.environ.update(BEITLAB_BL_SEND=str(tool), BEITLAB_BOARD_STATUS=str(out))
            try:
                self.assertEqual(BS.main(), 0)
            finally:
                os.environ.clear()
                os.environ.update(saved)
            report = json.loads(out.read_text())
            self.assertTrue(report["ok"])
            self.assertEqual(oct(out.stat().st_mode & 0o777), "0o644")

    def test_no_inspector_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = Path(tmp) / "bl_send"
            tool.write_text(f"#!{sys.executable}\nimport sys\nprint('echoed unchanged'); sys.exit(3)\n")
            tool.chmod(0o755)
            self.assertIsNone(BS.read_fields([str(tool)], wait_s=0.3))


if __name__ == "__main__":
    unittest.main()
