#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build the real policy/GPIO driver on the host with instrumented registers."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("hardware_profiles", REPO / "firmware/profiles.py")
profiles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiles)


class ManifestTest(unittest.TestCase):
    def test_default_is_carrier_and_unknown_profile_is_rejected(self):
        self.assertEqual(profiles.load_profile()["id"], "beitlab_robotics_rev011")
        for name in ("unknown", "", "../duos_bare", None):
            with self.subTest(name=name), self.assertRaises(profiles.ProfileError):
                profiles.load_profile(name)

    def test_child_replaces_inherited_permissions(self):
        carrier = profiles.load_profile()
        self.assertEqual(carrier["pins"]["A18"], {
            "owner": "robot", "purpose": "left knee servo PWM"})
        self.assertEqual(carrier["pins"]["A29"]["owner"], "application")
        self.assertEqual(carrier["pins"]["A16"]["owner"], "linux")

    def test_invalid_manifest_and_linux_override_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for src in profiles.PROFILE_DIR.glob("*.json"):
                shutil.copy(src, root)
            child = {"id": "test", "extends": "duos_bare", "description": "test",
                     "sources": ["test"], "pins": {}}
            variants = [
                {"pins": {"A018": {"owner": "robot", "purpose": "test"}}},
                {"pins": {"A16": {"owner": "robot", "purpose": "test"}}},
                {"pins": {"A1": {"owner": "application", "purpose": "test"}}},
                {"extends": "test"},
            ]
            for variant in variants:
                with self.subTest(variant=variant), self.assertRaises(profiles.ProfileError):
                    (root / "test.json").write_text(json.dumps(dict(child, **variant)))
                    profiles.load_profile("test", root)
            (root / "test.json").write_text('{"id":"test","id":"test"}')
            with self.assertRaisesRegex(profiles.ProfileError, "duplicate"):
                profiles.load_profile("test", root)


@unittest.skipUnless(shutil.which("cc"), "needs a host C compiler")
class RuntimeTest(unittest.TestCase):
    def test_both_profiles_without_hardware_access_on_refusal(self):
        for name in ("duos_bare", "beitlab_robotics_rev011"):
            with self.subTest(profile=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                profiles.generate_header(profiles.load_profile(name), root / "pg_profile_generated.h")
                (root / "FreeRTOS.h").write_text("/* host shim */\n")
                (root / "task.h").write_text(
                    "#define taskENTER_CRITICAL() ((void)0)\n"
                    "#define taskEXIT_CRITICAL() ((void)0)\n")
                (root / "gpio.h").write_text(
                    "#define CVI_GPIOA_BASE 0x03020000u\n"
                    "#define CVI_GPIOB_BASE 0x03021000u\n"
                    "#define CVI_GPIOC_BASE 0x03022000u\n"
                    "#define CVI_GPIOD_BASE 0x03023000u\n")
                (root / "mmio.h").write_text(
                    "#include <stdint.h>\n"
                    "uint32_t mmio_read_32(uintptr_t address);\n"
                    "void mmio_write_32(uintptr_t address, uint32_t value);\n")
                (root / "playground.h").write_text("void pg_print(const char *fmt, ...);\n")
                (root / "test.c").write_text(HARNESS)
                executable = root / "test"
                compiled = subprocess.run([
                    "cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                    "-I" + str(root), "-I" + str(REPO / "firmware/include"),
                    str(REPO / "firmware/runtime/pg_profile.c"),
                    str(REPO / "firmware/runtime/pg_gpio.c"), str(root / "test.c"),
                    "-o", str(executable)], capture_output=True, text=True)
                self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                result = subprocess.run([str(executable), name], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


HARNESS = r'''
#include <assert.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "pg_gpio.h"

static unsigned accesses;
static uint32_t dr = 0x00100002, ddr = 0x00100002;
static char message[256];
uint32_t mmio_read_32(uintptr_t address)
{
    accesses++;
    assert(address == 0x03020000 || address == 0x03020004);
    return address == 0x03020000 ? dr : ddr;
}
void mmio_write_32(uintptr_t address, uint32_t value)
{
    accesses++;
    assert(address == 0x03020000 || address == 0x03020004);
    if (address == 0x03020000) dr = value; else ddr = value;
}
void pg_print(const char *fmt, ...)
{
    va_list args;
    va_start(args, fmt);
    vsnprintf(message, sizeof(message), fmt, args);
    va_end(args);
}
static void refused(const char *pad)
{
    unsigned before = accesses;
    assert(pg_gpio_output(pad) == -1);
    assert(pg_gpio_write(pad, 1) == -1);
    assert(accesses == before);
    assert(strstr(message, pg_profile_name()));
}
int main(int argc, char **argv)
{
    assert(argc == 2);
    assert(!strcmp(pg_profile_name(), argv[1]));
    int carrier = !strcmp(argv[1], "beitlab_robotics_rev011");
    const char *invalid[] = {NULL, "", "A", "a29", "A-1", "A32", "F0",
        " A29", "A29 ", "A4294967325", "A9999999999999999999999999", "B19x"};
    for (unsigned i = 0; i < sizeof(invalid)/sizeof(invalid[0]); i++)
        refused(invalid[i]);
    refused("B19");
    refused("B019");
    if (carrier) assert(strstr(message, "MOT_EN_REQ"));
    refused("A16");
    assert(strstr(message, "Linux"));
    refused("A00016");
    refused("B6");
    refused("E0");
    refused("D31");
    if (carrier) {
        /* Independent regression list from board_carrier.h revision 0.11. */
        const char *reserved[] = {"B11", "B12", "B20", "E0", "E1", "E2",
            "A18", "A19", "B15", "B13", "B14", "B16", "B18", "B22", "B21",
            "B1", "B2", "A20", "B19", "C20", "A28", "B3", "C19", "C18"};
        for (unsigned i = 0; i < sizeof(reserved)/sizeof(reserved[0]); i++) {
            char alias[16];
            refused(reserved[i]);
            assert(strstr(message, "robot firmware"));
            snprintf(alias, sizeof(alias), "%c00%s", reserved[i][0], reserved[i] + 1);
            refused(alias);
            assert(strstr(message, "robot firmware"));
        }
    }
    /* Profile distinction: these pads are muxed GPIO on a bare Duo S only. */
    const char *header[] = {"A18", "A19", "A20", "A28"};
    for (unsigned i = 0; i < 4; i++) {
        if (carrier) refused(header[i]);
        else assert(pg_profile_gpio_output(header[i], NULL) == NULL);
    }
    assert(pg_profile_resource("uart0"));
    assert(strstr(pg_profile_resource("spi3"), carrier ? "robot" : "not verified"));
    assert(pg_profile_resource("unknown"));
    assert(pg_profile_resource(NULL));
    assert(pg_gpio_write("A29", 1) == -1);
    assert(accesses == 0);
    /* Caller may mutate or discard its pin-name buffer after initialization. */
    char pad[] = "A29";
    assert(pg_gpio_output(pad) == 0);
    strcpy(pad, "B19");
    assert(dr == 0x00100002 && ddr == (0x00100002 | (1u << 29)));
    unsigned before = accesses;
    assert(pg_gpio_output("A00029") == 0);
    assert(accesses == before); /* Canonical identity; no duplicate initialization. */
    assert(pg_gpio_write("A029", 1) == 0);
    assert(dr == (0x00100002 | (1u << 29)));
    assert(pg_gpio_write("A29", 0) == 0);
    assert(dr == 0x00100002);
    refused(pad); /* Mutating the original string did not authorize another pad. */
    return 0;
}
'''


if __name__ == "__main__":
    unittest.main()
