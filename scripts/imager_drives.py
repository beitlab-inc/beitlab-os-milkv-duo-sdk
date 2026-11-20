"""Read-only drive discovery and guarded whole-disk writes for the PC imager.

No device is opened for writing during discovery. Flashing requires an
interactive, exact device-name confirmation and root privileges.
"""
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import struct
import subprocess
import sys

CHUNK = 4 * 1024 * 1024


class FlashCancelled(KeyboardInterrupt):
    def __init__(self, writing_started=False):
        self.writing_started = writing_started
        super().__init__()


def command(argv):
    return subprocess.run(argv, check=True, capture_output=True).stdout


def walk(node):
    yield node
    for child in node.get("children", []):
        yield from walk(child)


@dataclass
class Drive:
    path: str
    size: int
    model: str
    identity: str
    mounts: list = field(default_factory=list)
    unmount: list = field(default_factory=list)
    reason: str = ""
    device_id: str = ""
    sequence: str = ""

    @property
    def eligible(self):
        return not self.reason


def linux_drives(data, protected_ids):
    drives = []
    for node in data.get("blockdevices", []):
        if node.get("type") != "disk":
            continue
        nodes = list(walk(node))
        mounts = [p for child in nodes for p in (child.get("mountpoints") or []) if p]
        reason = ""
        if not (node.get("rm") or node.get("tran") in ("usb", "mmc")):
            reason = "internal / not removable"
        elif any(child.get("maj:min") in protected_ids for child in nodes):
            reason = "contains the system, your home, or an imager source file"
        elif "[SWAP]" in mounts or any(child.get("type") not in ("disk", "part") for child in nodes):
            reason = "active swap or storage mapping"
        elif any(p == "/" or any(p == root or p.startswith(root + "/")
                                 for root in ("/boot", "/home", "/usr", "/var", "/etc")) for p in mounts):
            reason = "system disk"
        elif node.get("ro"):
            reason = "read-only"
        elif not node.get("size"):
            reason = "no media"
        identity = json.dumps([node.get(k) for k in ("path", "size", "model", "serial", "wwn", "maj:min", "diskseq")])
        drives.append(Drive(node["path"], int(node.get("size") or 0),
                            (node.get("model") or "Removable drive").strip(), identity,
                            mounts, [c["path"] for c in reversed(nodes) if any(c.get("mountpoints") or [])],
                            reason, node.get("maj:min", ""), str(node.get("diskseq") or "")))
    return drives


def protected_paths(extra):
    return [Path(p) for p in ("/", "/boot", "/boot/efi", "/home", "/usr", "/var", Path.home(), Path.cwd(),
                              Path(__file__).resolve(), *extra) if Path(p).exists()]


def scan_linux(extra):
    columns = "NAME,PATH,TYPE,SIZE,MODEL,SERIAL,WWN,TRAN,RM,RO,MOUNTPOINTS,MAJ:MIN"
    data = json.loads(command(["lsblk", "--json", "--bytes", "--output", columns]))
    protected = set()
    for path in protected_paths(extra):
        ids = command(["findmnt", "--noheadings", "--output", "MAJ:MIN", "--target", str(path)]).decode().split()
        if not ids:
            raise ValueError(f"cannot identify the filesystem containing {path}")
        protected.update(ids)
    for node in data.get("blockdevices", []):
        try:
            node["diskseq"] = (Path("/sys/class/block") / node["name"] / "diskseq").read_text().strip()
        except FileNotFoundError:
            node["diskseq"] = ""
    return linux_drives(data, protected)


def mac_physical(identifier, containers):
    """Resolve an APFS synthesized disk to its physical backing disks."""
    whole = re.sub(r"s\d+.*$", "", identifier.removeprefix("/dev/"))
    if whole in containers:
        return {re.sub(r"s\d+.*$", "", store["DeviceIdentifier"])
                for store in containers[whole].get("PhysicalStores", [])}
    return {whole}


def scan_macos(extra):
    listing = plistlib.loads(command(["diskutil", "list", "-plist", "physical"]))
    apfs = plistlib.loads(command(["diskutil", "apfs", "list", "-plist"]))
    containers = {c["ContainerReference"]: c for c in apfs.get("Containers", [])}
    protected = set()
    for path in protected_paths(extra):
        # df accepts ordinary files/directories; diskutil expects a device
        # or an actual volume mount point.
        source = command(["df", "-P", str(path)]).decode().splitlines()[-1].split()[0]
        if source.startswith("/dev/disk"):
            protected.update(mac_physical(source, containers))
    if not protected:
        raise ValueError("cannot identify the macOS system disk")
    drives = []
    for disk in listing.get("AllDisksAndPartitions", []):
        identifier = disk["DeviceIdentifier"]
        info = plistlib.loads(command(["diskutil", "info", "-plist", identifier]))
        reason = ""
        if identifier in protected:
            reason = "contains the system, your home, or an imager source file"
        elif info.get("Internal") is not False or info.get("Whole") is not True:
            reason = "internal / not a whole external disk"
        elif info.get("Writable") is not True:
            reason = "read-only"
        elif not info.get("TotalSize"):
            reason = "no media"
        identity = json.dumps([info.get(k) for k in ("DeviceIdentifier", "TotalSize", "MediaName", "DeviceTreePath", "DiskUUID")])
        mounts = [p["MountPoint"] for p in [disk, *disk.get("Partitions", [])] if p.get("MountPoint")]
        drives.append(Drive("/dev/" + identifier, int(info.get("TotalSize") or 0),
                            info.get("MediaName") or identifier, identity, mounts, reason=reason))
    return drives


def scan(extra=()):
    if sys.platform.startswith("linux"):
        return scan_linux(extra)
    if sys.platform == "darwin":
        return scan_macos(extra)
    raise ValueError("Drive flashing currently supports native Linux and macOS. Windows raw-disk flashing is not implemented.")


def selected_drive(path, identity, image):
    found = next((d for d in scan([image]) if d.path == path), None)
    if found is None or found.identity != identity:
        raise ValueError("the selected drive disappeared or changed; select it again")
    if not found.eligible:
        raise ValueError(f"refusing {path}: {found.reason}")
    if found.size < image.stat().st_size:
        raise ValueError("the image is larger than the selected drive")
    return found


def copy_and_verify(source, target, size, expected, progress):
    """Write an already-open target without truncation, then read it back.

    The caller validates and exclusively opens a real device. Keeping this
    byte-copy function separate lets tests exercise short writes and failed
    verification using ordinary temporary files, never real disks.
    """
    source.seek(0)
    done = 0
    while done < size:
        chunk = source.read(min(CHUNK, size - done))
        if not chunk:
            raise ValueError("image ended unexpectedly")
        pending = memoryview(chunk)
        while pending:
            count = target.write(pending)
            if count is None or count <= 0:
                raise OSError("drive stopped accepting writes")
            pending = pending[count:]
        done += len(chunk)
        progress("Writing", done, size)
    target.flush()
    os.fsync(target.fileno())
    target.seek(0)
    digest = hashlib.sha256()
    done = 0
    while done < size:
        chunk = target.read(min(CHUNK, size - done))
        if not chunk:
            raise ValueError("drive ended during verification")
        digest.update(chunk)
        done += len(chunk)
        progress("Verifying", done, size)
    if digest.hexdigest() != expected:
        raise ValueError("read-back verification failed; do not boot this card")


def flash(image, path, identity, expected, ui):
    state = {"writing": False}
    try:
        return _flash(image, path, identity, expected, ui, state)
    except (KeyboardInterrupt, EOFError):
        raise FlashCancelled(state["writing"]) from None


def _flash(image, path, identity, expected, ui, state):
    if not sys.stdin.isatty():
        raise ValueError("flashing requires an interactive terminal and device-name confirmation")
    if os.geteuid() != 0:
        raise PermissionError("the disk writer must run with administrator privileges")
    image = Path(image).resolve(strict=True)
    # Hash the same open file that will be written, before opening any disk.
    with image.open("rb", buffering=0) as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("source image must be a regular file")
        digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(CHUNK), b""):
            digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError("the prepared image changed; no drive was written")
        drive = selected_drive(path, identity, image)
        ui.say(f"Target: {drive.path} · {drive.model} · {drive.size / 1_000_000_000:.1f} GB")
        ui.say("Everything on this drive will be erased.", "⚠️")
        phrase = f"ERASE {drive.path}"
        while True:
            answer = ui.ask(f"Type {phrase} to flash (q to cancel)", default=None)
            if answer == phrase:
                break
            if not answer or answer.lower() in ("q", "quit", "cancel"):
                raise KeyboardInterrupt
            ui.say(f"Confirmation did not match. Enter the full phrase: {phrase}")
            ui.say("No data has been written. Try again, or enter q to cancel.")
        drive = selected_drive(path, identity, image)
        if sys.platform.startswith("linux"):
            for device in drive.unmount:
                command(["umount", "--", device])
            raw = drive.path
        else:
            command(["diskutil", "unmountDisk", drive.path])
            raw = drive.path.replace("/dev/disk", "/dev/rdisk", 1)
        drive = selected_drive(path, identity, image)
        if sys.platform.startswith("linux") and drive.mounts:
            raise ValueError("the drive was remounted; close applications and retry")
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | os.O_EXCL
        fd = os.open(raw, flags)
        with os.fdopen(fd, "r+b", buffering=0) as target:
            actual = os.fstat(fd)
            if sys.platform.startswith("linux"):
                import fcntl
                if not stat.S_ISBLK(actual.st_mode):
                    raise ValueError("target is not a block device")
                if f"{os.major(actual.st_rdev)}:{os.minor(actual.st_rdev)}" != drive.device_id:
                    raise ValueError("device identity changed before opening")
                capacity = struct.unpack("Q", fcntl.ioctl(fd, 0x80081272, bytes(8)))[0]
                if capacity != drive.size:
                    raise ValueError("device capacity changed before opening")
                if drive.sequence:
                    sequence = struct.unpack("Q", fcntl.ioctl(fd, 0x80081280, bytes(8)))[0]
                    if str(sequence) != drive.sequence:
                        raise ValueError("a different card was inserted; select the drive again")
            elif not stat.S_ISCHR(actual.st_mode):
                raise ValueError("target is not a macOS raw device")
            state["writing"] = True
            copy_and_verify(source, target, metadata.st_size, expected, ui.progress)
        if sys.platform == "darwin":
            command(["diskutil", "eject", drive.path])
        ui.say("Write and read-back verification complete. You can remove the card.", "✅")
