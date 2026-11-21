#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build a beitlab Robotics MilkV duo OS C906L ELF against the pinned vendor FreeRTOS tree.

This is the single place that knows how a playground program is compiled
and linked. It is used by scripts/build-image.sh (starter firmware) and by
the PlatformIO platform under examples/platformio/common/platform-duo-c906.

What it does, and why each step matches the SDK:

* Compiler flags are not written down here. They are parsed out of
  freertos/cvitek/scripts/toolchain-riscv64-elf.cmake (the file
  build_cv181x.sh passes as CMAKE_TOOLCHAIN_FILE), whose sha256 is pinned
  in sdk.lock. -march/-mabi therefore cannot drift from the SDK.
* The per-target flags come from freertos/cvitek/task/CMakeLists.txt
  (SAFETY_FLAGS and -D${RUN_TYPE} with RUN_TYPE=CVIRTOS), also parsed.
* The vendor libraries (arch kernel common hal driver) are the CVIRTOS
  stages of build_cv181x.sh, built once by scripts/build-rtos-libs.sh into
  freertos/cvitek/install. The vendor task stage (comm, rgn, audio) is not
  linked: firmware/runtime/playground_rt.c replaces task/comm.
* The vendor task/main sources (main(), vectors, tick setup) are compiled
  unchanged, so start-up is identical to cvirtos.elf.
* Linker script: freertos/cvitek/scripts/cv181x_lscript.ld, which INCLUDEs
  the cvi_board_memmap.ld generated from the board memmap.py.
* The result is checked against the remoteproc ABI (see check_elf()).

Usage:
  rtosbuild.py build --out hello.elf --name hello src/main.c [...]
  rtosbuild.py check file.elf
  rtosbuild.py flags            # print the parsed flags
"""

import argparse
import glob
import hashlib
import os
import re
import shlex
import struct
import subprocess
import sys

from profiles import DEFAULT_PROFILE, ProfileError, generate_header, load_profile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
FREERTOS_LIBS = ["arch", "kernel", "common", "hal", "driver"]


class BuildError(Exception):
    pass


# --------------------------------------------------------------------------
# sdk.lock and paths

def read_lock(repo=REPO):
    lock = {}
    with open(os.path.join(repo, "sdk.lock")) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if sep:
                lock[key.strip()] = value.strip()
    return lock


def sdk_dir():
    work = os.environ.get("BEITLAB_WORK_DIR", os.path.join(
        os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "beitlab-duo-os"))
    return os.path.abspath(os.environ.get("PLAYGROUND_SDK", os.path.join(work, "sdk")))


def toolchain_bin(sdk, lock):
    env = os.environ.get("PLAYGROUND_RISCV_TOOLCHAIN")
    if env:
        return os.path.abspath(env)
    recorded = os.path.join(sdk, "PLAYGROUND_TOOLCHAIN")  # written by fetch-freertos.sh
    if os.path.isfile(recorded):
        return open(recorded).read().strip()
    return os.path.join(sdk, "host-tools", lock["host_tools_riscv64_elf"], "bin")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_sdk(sdk, lock):
    """Refuse to build against an SDK tree that is not the pinned one."""
    tc_file = os.path.join(sdk, lock["toolchain_file"])
    if not os.path.isfile(tc_file):
        raise BuildError(
            "%s is missing.\nRun scripts/fetch-freertos.sh first." % tc_file)
    got = sha256_file(tc_file)
    if got != lock["toolchain_file_sha256"]:
        raise BuildError(
            "%s does not match sdk.lock\n  expected %s\n  found    %s"
            % (tc_file, lock["toolchain_file_sha256"], got))
    try:
        head = subprocess.check_output(
            ["git", "-C", sdk, "rev-parse", "HEAD"], text=True,
            stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        head = None
    if head is not None and head != lock["sdk_commit"]:
        raise BuildError("sdk/ is at %s, sdk.lock pins %s" % (head, lock["sdk_commit"]))


# --------------------------------------------------------------------------
# Flag extraction from the vendor CMake files

_SET_FLAGS = re.compile(r'^\s*set\(\s*CMAKE_C_FLAGS\s+"\$\{CMAKE_C_FLAGS\}\s*([^"]*)"\s*\)\s*$')
_CROSS = re.compile(r'^\s*set\(\s*CROSS_COMPILE\s+(\S+)\s*\)\s*$')
_IF_ENV = re.compile(r'^\s*if\s*\(\s*\$ENV\{(\w+)\}\s+STREQUAL\s+"([^"]*)"\s*\)\s*$')


def parse_toolchain_cmake(path, environ=None):
    """Return (cross_compile_prefix, [flags]) as CMake would evaluate them.

    Handles the one conditional in the pinned file,
      if ($ENV{DDR_64MB_SIZE} STREQUAL "y") ... else() ... endif()
    by evaluating it against `environ` (default: os.environ), like CMake.
    """
    environ = os.environ if environ is None else environ
    flags, cross = [], None
    stack = []  # list of booleans: is the current branch active
    with open(path) as f:
        for raw in f:
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            m = _IF_ENV.match(line)
            if m:
                stack.append(environ.get(m.group(1), "") == m.group(2))
                continue
            if re.match(r"^\s*else\s*\(\s*\)\s*$", line):
                if not stack:
                    raise BuildError("%s: else() without if()" % path)
                stack[-1] = not stack[-1]
                continue
            if re.match(r"^\s*endif\s*\(\s*\)\s*$", line):
                stack.pop()
                continue
            if re.match(r"^\s*if\s*\(", line):
                raise BuildError("%s: unsupported if(): %s" % (path, line.strip()))
            if stack and not all(stack):
                continue
            m = _CROSS.match(line)
            if m:
                cross = m.group(1)
                continue
            m = _SET_FLAGS.match(line)
            if m:
                flags.extend(shlex.split(m.group(1)))
    if stack:
        raise BuildError("%s: unterminated if()" % path)
    if not cross:
        raise BuildError("%s: CROSS_COMPILE not found" % path)
    if not any(f.startswith("-march=") for f in flags):
        raise BuildError("%s: no -march in CMAKE_C_FLAGS" % path)
    return cross, flags


def parse_task_flags(path):
    """SAFETY_FLAGS and -D${RUN_TYPE} from freertos/cvitek/task/CMakeLists.txt."""
    text = open(path).read()
    m = re.search(r'set\(\s*SAFETY_FLAGS\s+"([^"]*)"\s*\)', text)
    if not m:
        raise BuildError("%s: SAFETY_FLAGS not found" % path)
    if '-D${RUN_TYPE}' not in text:
        raise BuildError("%s: -D${RUN_TYPE} not found" % path)
    return shlex.split(m.group(1)) + ["-DCVIRTOS"]


# --------------------------------------------------------------------------
# Configuration

class Config:
    def __init__(self, sdk=None, toolchain=None):
        self.lock = read_lock()
        self.sdk = os.path.abspath(sdk or sdk_dir())
        verify_sdk(self.sdk, self.lock)
        self.top = os.path.join(self.sdk, "freertos", "cvitek")
        self.board = self.lock["board_link"]
        self.tc_bin = os.path.abspath(toolchain or toolchain_bin(self.sdk, self.lock))
        self.cross, self.toolchain_flags = parse_toolchain_cmake(
            os.path.join(self.sdk, self.lock["toolchain_file"]),
            dict(os.environ, DDR_64MB_SIZE=self.ddr_64mb()))
        self.task_flags = parse_task_flags(os.path.join(self.top, "task", "CMakeLists.txt"))
        self.install = os.path.join(self.top, "install")
        self.memmap_dir = os.path.join(self.sdk, "build", "output", self.board)
        self.lscript = os.path.join(self.top, "scripts", "cv181x_lscript.ld")

    def ddr_64mb(self):
        """DDR_64MB_SIZE as the SDK exports it from build/.config (y or n);
        scripts/build-rtos-libs.sh sets the same value for the CMake stages."""
        try:
            with open(os.path.join(self.sdk, "build", ".config")) as f:
                return "y" if "CONFIG_DDR_64MB_SIZE=y\n" in f.read() else "n"
        except OSError:
            return "n"

    def tool(self, name):
        path = os.path.join(self.tc_bin, self.cross + name)
        if not os.path.exists(path):
            raise BuildError(
                "%s not found.\nRun scripts/fetch-freertos.sh, or set "
                "PLAYGROUND_RISCV_TOOLCHAIN to the bin/ directory of the SDK "
                "riscv64-elf toolchain." % path)
        return path

    def require_libs(self):
        missing = [l for l in FREERTOS_LIBS
                   if not os.path.isfile(os.path.join(self.install, "lib", "lib%s.a" % l))]
        if missing or not os.path.isfile(os.path.join(self.memmap_dir, "cvi_board_memmap.ld")):
            script = os.path.join(REPO, "scripts", "build-rtos-libs.sh")
            print("rtosbuild: vendor FreeRTOS libraries missing (%s); running %s"
                  % (" ".join(missing) or "memmap", script), file=sys.stderr)
            subprocess.check_call([script], env=dict(os.environ, PLAYGROUND_SDK=self.sdk,
                                                     PLAYGROUND_RISCV_TOOLCHAIN=self.tc_bin))

    def include_dirs(self):
        """Every directory under install/ that holds a header, sorted like
        the vendor HEADER_DIRECTORIES macro in task/main/CMakeLists.txt."""
        dirs = []
        for h in sorted(glob.glob(os.path.join(self.install, "**", "*.h"), recursive=True)):
            d = os.path.dirname(h)
            if d not in dirs:
                dirs.append(d)
        # The pinned SDK does not install the DesignWare I2C headers with
        # its default CVIRTOS stages. Read them from the sparse source tree;
        # the polling example does not link the vendor IRQ-based I2C driver.
        return [os.path.join(REPO, "firmware", "include")] + dirs + [
            os.path.join(self.top, "hal", "cv181x", "i2c", "include"),
            os.path.join(self.top, "driver", "i2c", "include")]

    def runtime_sources(self):
        # Same glob as task/main/CMakeLists.txt: file(GLOB _SOURCES "src/*.c" "*.S").
        # Note "*.S" is the task/main root, not src/: src/FreeRTOS_asm_vectors.S
        # is ARM (Xilinx demo) assembly and is never built by the vendor.
        main_dir = os.path.join(self.top, "task", "main")
        vendor = sorted(glob.glob(os.path.join(main_dir, "src", "*.c")) +
                        glob.glob(os.path.join(main_dir, "*.S")))
        return vendor + [os.path.join(REPO, "firmware", "runtime", source)
                         for source in ("playground_rt.c", "pg_profile.c", "pg_gpio.c")]

    def cflags(self):
        return self.toolchain_flags + self.task_flags

    def defines(self, name, version):
        return ['-DPLAYGROUND_VERSION="%s"' % version,
                '-DPLAYGROUND_PROGRAM="%s"' % name]

    def link_args(self):
        return (["-T" + self.lscript, "-L" + self.memmap_dir,
                 "-L" + os.path.join(self.install, "lib"),
                 "-Wl,--start-group"] + ["-l" + l for l in FREERTOS_LIBS] +
                ["-lm", "-lc", "-lgcc", "-lsim", "-Wl,--end-group"])

    def memory_region(self):
        """(boot_addr, region_base, region_size) from the generated memmap."""
        vals = {}
        with open(os.path.join(self.memmap_dir, "cvi_board_memmap.ld")) as f:
            for line in f:
                m = re.match(r"\s*(CVIMMAP_\w+)\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*;", line)
                if m:
                    vals[m.group(1)] = int(m.group(2), 0)
        return (vals["CVIMMAP_FSBL_C906L_START_ADDR"], vals["CVIMMAP_FREERTOS_ADDR"],
                vals["CVIMMAP_FREERTOS_SIZE"])


# --------------------------------------------------------------------------
# ELF check (mirrors agent/elfcheck.c)

EM_RISCV = 243
PT_LOAD = 1
EF_RISCV_FLOAT_ABI = 0x6
EF_RISCV_FLOAT_ABI_DOUBLE = 0x4


def check_elf(path, boot_addr, base, size):
    data = open(path, "rb").read()
    errs = []
    if data[:4] != b"\x7fELF":
        return ["not an ELF file"]
    if data[4] != 2:
        errs.append("not ELF64")
    if data[5] != 1:
        errs.append("not little endian")
    if errs:
        return errs
    (e_type, e_machine, _ver, e_entry, e_phoff, e_shoff, e_flags, _ehsize,
     e_phentsize, e_phnum, e_shentsize, e_shnum, e_shstrndx) = struct.unpack_from(
        "<HHIQQQIHHHHHH", data, 16)
    if e_machine != EM_RISCV:
        errs.append("e_machine %d is not RISC-V" % e_machine)
    if e_type != 2:
        errs.append("not ET_EXEC")
    if e_flags & EF_RISCV_FLOAT_ABI != EF_RISCV_FLOAT_ABI_DOUBLE:
        errs.append("float ABI is not lp64d (e_flags 0x%x)" % e_flags)
    if e_entry != boot_addr:
        errs.append("entry 0x%x != C906L boot address 0x%x" % (e_entry, boot_addr))
    loads = 0
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type, _flags, p_offset, _vaddr, p_paddr, p_filesz, p_memsz, _align = \
            struct.unpack_from("<IIQQQQQQ", data, off)
        if p_type != PT_LOAD or p_memsz == 0:
            continue
        loads += 1
        if p_filesz > p_memsz:
            errs.append("segment %d filesz > memsz" % i)
        if p_offset + p_filesz > len(data):
            errs.append("segment %d truncated" % i)
        if p_paddr < base or p_paddr + p_memsz > base + size:
            errs.append("segment %d [0x%x, 0x%x) outside carveout [0x%x, 0x%x)"
                        % (i, p_paddr, p_paddr + p_memsz, base, base + size))
    if loads == 0:
        errs.append("no PT_LOAD segment")
    # .text must exist and hold the entry (the vendor lscript puts .vectors
    # and _start first in .text).
    have_text = False
    if e_shnum and e_shentsize == 64 and e_shstrndx < e_shnum:
        str_off, str_size = struct.unpack_from("<QQ", data, e_shoff + e_shstrndx * 64 + 24)
        names = data[str_off:str_off + str_size]
        for i in range(e_shnum):
            sh_name, sh_type, sh_flags, sh_addr, _o, sh_size = struct.unpack_from(
                "<IIQQQQ", data, e_shoff + i * 64)
            if names[sh_name:sh_name + 6] == b".text\0" and sh_type == 1 and \
                    sh_flags & 0x4 and sh_addr <= e_entry < sh_addr + sh_size:
                have_text = True
    if not have_text:
        errs.append("no executable .text section containing the entry point")
    return errs


# --------------------------------------------------------------------------
# Build

def run(cmd, verbose):
    if verbose:
        print(" ".join(shlex.quote(c) for c in cmd))
    subprocess.check_call(cmd)


def build(cfg, name, version, sources, out, extra_includes=(), verbose=False,
          profile=DEFAULT_PROFILE):
    try:
        policy = load_profile(profile)
    except ProfileError as exc:
        raise BuildError(str(exc)) from exc
    cfg.require_libs()
    gcc = cfg.tool("gcc")
    objdir = os.path.join(os.path.dirname(os.path.abspath(out)), "obj-" + name)
    os.makedirs(objdir, exist_ok=True)
    generate_header(policy, os.path.join(objdir, "pg_profile_generated.h"))
    print("rtosbuild: hardware profile %s" % policy["id"])
    incs = ["-I" + objdir] + ["-I" + os.path.abspath(d) for d in extra_includes] + \
           ["-I" + d for d in cfg.include_dirs()]
    base_flags = cfg.cflags() + cfg.defines(name, version) + incs
    objs = []
    for src in list(sources) + cfg.runtime_sources():
        src = os.path.abspath(src)
        rel = os.path.relpath(src, "/").replace(os.sep, "_")
        obj = os.path.join(objdir, rel + ".o")
        run([gcc] + base_flags + ["-c", src, "-o", obj], verbose)
        objs.append(obj)
    out = os.path.abspath(out)
    run([gcc] + cfg.cflags() + objs + ["-o", out] + cfg.link_args() +
        ["-Wl,-Map=" + out + ".map"], verbose)
    errs = check_elf(out, *cfg.memory_region())
    if errs:
        raise BuildError("%s violates the remoteproc ABI:\n  %s" % (out, "\n  ".join(errs)))
    print("rtosbuild: %s ok (%d bytes)" % (out, os.path.getsize(out)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sdk", help="SDK checkout (default: PLAYGROUND_SDK or the external SDK cache)")
    ap.add_argument("--toolchain", help="bin/ of the riscv64-elf toolchain")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", required=True)
    b.add_argument("--name", required=True)
    b.add_argument("--profile", default=DEFAULT_PROFILE,
                   help="hardware profile from profiles/ (default: %(default)s)")
    b.add_argument("--version", default=open(os.path.join(REPO, "VERSION")).read().strip())
    b.add_argument("-I", dest="includes", action="append", default=[])
    b.add_argument("-v", "--verbose", action="store_true")
    b.add_argument("sources", nargs="+")
    c = sub.add_parser("check")
    c.add_argument("elf")
    sub.add_parser("flags")
    args = ap.parse_args(argv)
    try:
        cfg = Config(args.sdk, args.toolchain)
        if args.cmd == "flags":
            print("CROSS_COMPILE=" + cfg.cross)
            print("CFLAGS=" + " ".join(cfg.cflags()))
            print("LDFLAGS=" + " ".join(cfg.link_args()))
            return 0
        if args.cmd == "check":
            errs = check_elf(args.elf, *cfg.memory_region())
            for e in errs:
                print("%s: %s" % (args.elf, e))
            return 1 if errs else 0
        build(cfg, args.name, args.version, args.sources, args.out,
              args.includes, args.verbose, profile=args.profile)
        return 0
    except (BuildError, subprocess.CalledProcessError) as e:
        print("rtosbuild: error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
