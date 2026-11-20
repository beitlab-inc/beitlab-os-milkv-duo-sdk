#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Inspect the built Duo S device tree (acceptance test 8).

  check-dtb.py <decompiled.dts> [--carrier board_carrier.h] [--base 0x.. --size 0x..]

Checks, on the dtb the kernel boots with:
  1. a cvitek,cv18xx-c906l-rproc node, enabled, firmware-name
     "playground.elf", whose memory-region is a no-map reserved region at
     the board memmap's CVIMMAP_FREERTOS_ADDR/SIZE;
  2. spi3, i2c4, pwm0-2, vo and mipi_tx are disabled;
  3. no enabled node references a GPIO on a pad that board_carrier.h
     assigns (every *-gpio/*-gpios property is resolved to a pad name);
  4. ethernet, the SD controller, uart0 and rtos_cmdqu are still enabled.
Prints a report and exits non-zero on any failure.
"""
import argparse
import json
import re
import sys

GPIO_PORTS = {"3020000": "A", "3021000": "B", "3022000": "C", "3023000": "D", "5021000": "E"}
MUST_DISABLE = {
    "spi3": r"spi3@41b0000",
    "i2c4": r"i2c@4040000",
    "pwm0": r"pwm@3060000",
    "pwm1": r"pwm@3061000",
    "pwm2": r"pwm@3062000",
    "vo": r"vo",
    "mipi_tx": r"mipi_tx",
}
MUST_ENABLE = {
    "ethernet": r"ethernet@4070000",
    "sd": r"cv-sd@4310000",
    "uart0": r"serial@4140000",
    "rtos_cmdqu": r"rtos_cmdqu",
}


class Node:
    def __init__(self, name, parent):
        self.name, self.parent, self.props, self.children = name, parent, {}, []

    def path(self):
        return (self.parent.path().rstrip("/") + "/" + self.name) if self.parent else "/"

    def enabled(self):
        n = self
        while n:
            if n.props.get("status", '"okay"').strip('"') not in ("okay", "ok"):
                return False
            n = n.parent
        return True


def parse(text):
    root, cur = None, None
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"^([\w,.+@/-]+)\s*\{$", line) or re.match(r"^[\w-]+:\s*([\w,.+@-]+)\s*\{$", line)
        if m:
            n = Node(m.group(1), cur)
            if cur:
                cur.children.append(n)
            else:
                root = n
            cur = n
            continue
        if line == "};":
            cur = cur.parent
            continue
        m = re.match(r"^([\w,.#+-]+)\s*=\s*(.*);$", line)
        if m and cur:
            cur.props[m.group(1)] = m.group(2)
        elif re.match(r"^[\w,.#+-]+;$", line) and cur:
            cur.props[line[:-1]] = True
    return root


def walk(n):
    yield n
    for c in n.children:
        yield from walk(c)


def cells(v):
    return [int(x, 16) for x in re.findall(r"0x[0-9a-fA-F]+", v)]


def carrier_pads(path):
    pads = set()
    for line in open(path):
        # I2C4 pads are named only in a comment: "(B1 SCL J4-50, B2 SDA J4-48)"
        pads.update(re.findall(r"\b([A-E]\d{1,2}) (?:SCL|SDA)\b", line))
        if not line.startswith("#define BAL_"):
            continue
        pads.update(re.findall(r'"([A-E]\d{1,2})"', line))
        m = re.search(r"/\*\s*([A-E]\d{1,2}),", line)
        if m:
            pads.add(m.group(1))
    return pads


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dts")
    ap.add_argument("--carrier")
    ap.add_argument("--carrier-profile", help="in-repo carrier pin snapshot for standalone CI builds")
    ap.add_argument("--base", type=lambda s: int(s, 0))
    ap.add_argument("--size", type=lambda s: int(s, 0))
    a = ap.parse_args()
    root = parse(open(a.dts).read())
    nodes = list(walk(root))
    by_phandle = {}
    for n in nodes:
        if "phandle" in n.props:
            by_phandle[cells(n.props["phandle"])[0]] = n
    fails, report = [], []

    def ok(msg):
        report.append("ok:   " + msg)

    def bad(msg):
        report.append("FAIL: " + msg)
        fails.append(msg)

    # 1. remoteproc
    rprocs = [n for n in nodes if n.props.get("compatible") == '"cvitek,cv18xx-c906l-rproc"']
    if len(rprocs) != 1:
        bad("expected one cvitek,cv18xx-c906l-rproc node, found %d" % len(rprocs))
    else:
        r = rprocs[0]
        (ok if r.enabled() else bad)("rproc node %s enabled" % r.path())
        fw = r.props.get("firmware-name", "")
        (ok if fw == '"playground.elf"' else bad)("firmware-name = %s" % fw)
        mr = by_phandle.get(cells(r.props.get("memory-region", "<>"))[0] if cells(r.props.get("memory-region", "<>")) else -1)
        if not mr:
            bad("memory-region phandle does not resolve")
        else:
            reg = cells(mr.props.get("reg", ""))
            base, size = (reg[0] << 32 | reg[1], reg[2] << 32 | reg[3]) if len(reg) == 4 else (None, None)
            (ok if "no-map" in mr.props else bad)("carveout %s is no-map" % mr.path())
            if a.base is not None:
                (ok if (base, size) == (a.base, a.size) else bad)(
                    "carveout reg 0x%x+0x%x matches memmap 0x%x+0x%x"
                    % (base or 0, size or 0, a.base, a.size))

    # 2/4. status. Vendor unit names carry leading zeros, "0x" and upper
    # case (spi3@041B0000, rtos_dram@0x9fe00000); compare normalized.
    def norm(name):
        base, _, unit = name.lower().partition("@")
        return base + ("@" + (re.sub(r"^(0x)?0*", "", unit) or "0") if unit else "")

    def find(pattern):
        return [n for n in nodes if re.fullmatch(pattern, norm(n.name))]
    for label, pat in MUST_DISABLE.items():
        found = find(pat)
        if not found:
            bad("%s (%s) not found" % (label, pat))
        for n in found:
            (ok if not n.enabled() else bad)("%s %s disabled" % (label, n.path()))
    for label, pat in MUST_ENABLE.items():
        found = find(pat)
        (ok if found and all(n.enabled() for n in found) else bad)("%s enabled" % label)

    # 3. GPIO claims
    ports = {}
    for n in nodes:
        if "gpio-controller" in n.props and "phandle" in n.props and n.parent:
            m = re.search(r"@(?:0x)?0*([0-9a-f]+)$", n.parent.name.lower())
            if m and m.group(1) in GPIO_PORTS:
                ports[cells(n.props["phandle"])[0]] = GPIO_PORTS[m.group(1)]
    claims = []
    for n in nodes:
        if not n.enabled():
            continue
        for k, v in n.props.items():
            if not (k.endswith("-gpio") or k.endswith("-gpios") or k.endswith(",irq-gpio") or k.endswith(",rst-gpio")) or v is True:
                continue
            c = cells(v)
            for i in range(0, len(c) - 1, 3):
                if c[i] in ports:
                    claims.append(("%s%d" % (ports[c[i]], c[i + 1]), n.path(), k))
    report.append("GPIO references in enabled nodes:")
    for pad, p, k in sorted(set(claims)):
        report.append("      %-4s %s %s" % (pad, p, k))
    if a.carrier or a.carrier_profile:
        pads = carrier_pads(a.carrier) if a.carrier else set()
        if a.carrier_profile:
            profile = json.load(open(a.carrier_profile))
            snapshot = {pad for pad, config in profile["pins"].items() if config["owner"] == "robot"}
            if not snapshot:
                bad("carrier profile has no robot pin reservations")
            pads.update(snapshot)
        report.append("carrier pads: %s" % " ".join(sorted(pads)))
        hit = [c for c in claims if c[0] in pads]
        if hit:
            for pad, p, k in hit:
                bad("carrier pad %s claimed by %s %s" % (pad, p, k))
        else:
            ok("no enabled node claims a carrier pad")
    print("\n".join(report))
    print("RESULT: %s" % ("FAIL" if fails else "PASS"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
