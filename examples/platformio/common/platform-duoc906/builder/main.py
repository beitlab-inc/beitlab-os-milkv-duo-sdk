# SPDX-License-Identifier: MIT
"""PlatformIO builder for beitlab Robotics MilkV duo OS C906L programs.

Compiling and linking is done by firmware/rtosbuild.py, the same code that
builds the starter firmware in the SD image, so an example and the starter
get identical flags (parsed from the pinned toolchain-riscv64-elf.cmake),
start-up code, vendor libraries and linker script. rtosbuild calls the
riscv64-unknown-elf-gcc that scripts/fetch-freertos.sh fetched.

Targets:
  (default)          build firmware.elf
  upload             upload_command (common/upload.py) with $SOURCE = ELF
  rtmonitor          custom_monitor (common/monitor.py): C906 log over SSH
"""
import glob
import os
import subprocess
import sys

from SCons.Script import ARGUMENTS, Default, DefaultEnvironment  # pylint: disable=import-error

env = DefaultEnvironment()

PROJECT_DIR = env.subst("$PROJECT_DIR")
# Resolve the local platform symlink so nested example folders use the same repo.
REPO = os.environ.get("PLAYGROUND_REPO") or os.path.abspath(
    os.path.join(os.path.realpath(env.PioPlatform().get_dir()), "..", "..", "..", ".."))
if not os.path.isfile(os.path.join(REPO, "firmware", "rtosbuild.py")):
    sys.stderr.write("Cannot find duo-s-playground at %s; set PLAYGROUND_REPO.\n" % REPO)
    env.Exit(1)
sys.path.insert(0, os.path.join(REPO, "firmware"))
import rtosbuild  # noqa: E402  pylint: disable=wrong-import-position

NAME = env.subst("$PIOENV")
SRC_DIR = env.subst("$PROJECT_SRC_DIR")
SOURCES = sorted(glob.glob(os.path.join(SRC_DIR, "**", "*.c"), recursive=True) +
                 glob.glob(os.path.join(SRC_DIR, "**", "*.S"), recursive=True))
INCLUDES = [PROJECT_DIR, env.subst("$PROJECT_INCLUDE_DIR"),
            os.path.join(REPO, "examples", "platformio", "common")]

# Shared libraries: `custom_libs = beitlabduino` in an example's platformio.ini
# compiles common/lib/beitlabduino/*.c and *.S with the program and adds that
# folder to the include path. Names are separated by spaces or commas.
LIB_ROOT = os.path.join(REPO, "examples", "platformio", "common", "lib")
libraries = env.GetProjectOption("custom_libs", "").replace(",", " ").split()
# beitlabduino_imu selects the carrier BMI323 or the J3 BMI160 from the
# profile at run time, so it links both drivers.
if "beitlabduino_imu" in libraries:
    libraries += [lib for lib in ("carrier_imu", "beitlabduino_bmi160")
                  if lib not in libraries]
# Preserve existing carrier example/user configurations after sharing the
# bounded I2C4 transport with standalone sensors.
if ({"carrier_imu", "beitlabduino_bmi160"} & set(libraries)
        and "duos_i2c4" not in libraries):
    libraries.append("duos_i2c4")
for _lib in libraries:
    _dir = os.path.join(LIB_ROOT, _lib)
    if not os.path.isdir(_dir):
        sys.stderr.write("custom_libs: no library %r in %s\n" % (_lib, LIB_ROOT))
        env.Exit(1)
    SOURCES += sorted(glob.glob(os.path.join(_dir, "*.c")) +
                      glob.glob(os.path.join(_dir, "*.S")))
    INCLUDES.append(_dir)
ELF = os.path.join(env.subst("$BUILD_DIR"), "firmware.elf")
VERSION = open(os.path.join(REPO, "VERSION")).read().strip()


def _build(target, source, env):  # pylint: disable=unused-argument,redefined-outer-name
    try:
        cfg = rtosbuild.Config()
        rtosbuild.build(cfg, NAME, VERSION, [str(s) for s in source], str(target[0]),
                        INCLUDES, verbose=int(ARGUMENTS.get("PIOVERBOSE", 0)) > 0,
                        profile=env.GetProjectOption("custom_profile", rtosbuild.DEFAULT_PROFILE))
    except (rtosbuild.BuildError, subprocess.CalledProcessError) as exc:
        sys.stderr.write("rtosbuild: error: %s\n" % exc)
        return 1
    return 0


if not SOURCES:
    sys.stderr.write("No sources in %s\n" % SRC_DIR)
    env.Exit(1)

firmware = env.Command(ELF, SOURCES, env.VerboseAction(_build, "Building $TARGET"))
env.AlwaysBuild(firmware)
Default(firmware)

env.AddPlatformTarget(
    "upload", firmware, env.VerboseAction("$UPLOADCMD", "Uploading $SOURCE"),
    "Upload", "Copy the ELF to the board and run rtos-agent load")

monitor_cmd = env.GetProjectOption("custom_monitor", "")
if monitor_cmd:
    env.AddPlatformTarget(
        "rtmonitor", None, env.VerboseAction(monitor_cmd, "C906 log over SSH (Ctrl-C to stop)"),
        "RT Monitor", "Stream `rtos-agent log -f` from the board")
