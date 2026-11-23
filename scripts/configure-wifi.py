#!/usr/bin/env python3
"""Prepare Wi-Fi, hostname, regular-user credentials, SSH keys and the Studio link before first boot."""
import argparse
import base64
import getpass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import struct
import subprocess
import tempfile
import urllib.parse

# Board variants (PROMPT.md 0.5.0): --board name -> boot-volume playground/board value.
BOARDS = {"duos-standalone": "duos_standalone", "robotics-board": "robotics_board_rev011"}
BOARD_LABEL = {"duos_standalone": "Milk-V Duo S (standalone image)",
               "robotics_board_rev011": "Beitlab Robotics Board rev 0.11 image"}
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
# Which boot-volume marker each provisioning file needs.
MARKERS = {"wifi.conf": "wifi-supported", "hostname": "setup-supported", "authorized_keys": "user-supported",
           "user.conf": "user-supported", "studio.conf": "studio-supported", "device-secret": "studio-supported"}
MARKER_VERSION = {"wifi-supported": "0.2.0", "setup-supported": "0.3.0", "user-supported": "0.4.0",
                  "studio-supported": "0.5.0"}


def profile(ssid, password, country=None):
    ssid_bytes = ssid.encode("utf-8")
    password_bytes = password.encode("utf-8")
    if not 1 <= len(ssid_bytes) <= 32 or any(c in ssid for c in "\x00\r\n"):
        raise ValueError("SSID must contain 1–32 UTF-8 bytes without NUL or newlines")
    if not 8 <= len(password_bytes) <= 63 or any(c in password for c in "\x00\r\n"):
        raise ValueError("WPA/WPA2 Personal password must contain 8–63 UTF-8 bytes without NUL or newlines")
    if country is not None:
        country = country.upper()
        if len(country) != 2 or not country.isascii() or not country.isalpha():
            raise ValueError("country must be a two-letter code, e.g. CO")
    psk = hashlib.pbkdf2_hmac("sha1", password_bytes, ssid_bytes, 4096, 32).hex()
    return ("ctrl_interface=/run/wpa_supplicant\nupdate_config=0\n"
            + (f"country={country}\n" if country else "")
            + "network={\n"
            + f"    ssid={ssid_bytes.hex()}\n"
            + "    scan_ssid=1\n    key_mgmt=WPA-PSK\n"
            + f"    psk={psk}\n}}\n")


def boot_offset(path):
    with path.open("rb") as image:
        mbr = image.read(512)
        if len(mbr) != 512 or mbr[510:] != b"\x55\xaa":
            raise ValueError("image has no DOS partition table")
        entry = mbr[446:462]
        start, sectors = struct.unpack_from("<II", entry, 8)
        if entry[4] not in (0x0B, 0x0C) or not start or not sectors:
            raise ValueError("first partition must be the Duo S FAT boot volume")
        if (start + sectors) * 512 > path.stat().st_size:
            raise ValueError("boot partition extends beyond the image")
        image.seek(start * 512)
        boot = image.read(512)
        # The SDK uses partition type 0x0c even when mkdosfs chooses FAT16.
        label = boot[71:82] if struct.unpack_from("<H", boot, 22)[0] == 0 else boot[43:54]
        if label != b"boot       " or boot[510:] != b"\x55\xaa":
            raise ValueError("first partition is not labeled boot")
    return start * 512


def private_write(path, content):
    # Do not follow/overwrite an existing symlink or profile accidentally.
    with path.open("x", encoding="utf-8") as output:
        os.chmod(path, 0o600)
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


def hostname_config(name):
    name = name.lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", name):
        raise ValueError("hostname must contain 1–63 letters, digits or hyphens; no .local suffix")
    return name + "\n"


def public_keys(paths):
    keys = []
    supported = {"ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384",
                 "ecdsa-sha2-nistp521", "sk-ssh-ed25519@openssh.com",
                 "sk-ecdsa-sha2-nistp256@openssh.com"}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 2)
            try:
                if len(parts) < 2 or parts[0] not in supported:
                    raise ValueError()
                data = base64.b64decode(parts[1], validate=True)
                offset = 0

                def field():
                    nonlocal offset
                    size = struct.unpack_from(">I", data, offset)[0]
                    offset += 4
                    if size > len(data) - offset:
                        raise ValueError()
                    value = data[offset:offset + size]
                    offset += size
                    return value

                if field() != parts[0].encode():
                    raise ValueError()
                if "ed25519" in parts[0]:
                    if len(field()) != 32:
                        raise ValueError()
                elif parts[0] == "ssh-rsa":
                    if not field() or not field():
                        raise ValueError()
                else:
                    curve = field()
                    point_sizes = {b"nistp256": 65, b"nistp384": 97, b"nistp521": 133}
                    point = field()
                    if curve.decode("ascii") not in parts[0] or len(point) != point_sizes.get(curve) or point[:1] != b"\x04":
                        raise ValueError()
                if parts[0].startswith("sk-"):
                    if not field():
                        raise ValueError()
                if offset != len(data):
                    raise ValueError()
            except (ValueError, struct.error) as error:
                raise ValueError(f"{path}: expected an OpenSSH public key (.pub), never a private key") from error
            if line not in keys:
                keys.append(line)
    if not keys:
        raise ValueError("SSH key files contain no public keys")
    return "\n".join(keys) + "\n"


def user_profile(password=None, password_login=False):
    if password_login and password is None:
        raise ValueError("SSH password login requires --user-password")
    hashed = ""
    if password is not None:
        if len(password) < 12 or len(password.encode()) > 128 or any(c in password for c in "\x00\r\n"):
            raise ValueError("user password must contain 12–128 characters/bytes without NUL or newlines")
        hashed = subprocess.run(["openssl", "passwd", "-6", "-stdin"],
                                input=password + "\n", capture_output=True, text=True, check=True).stdout.strip()
        if not re.fullmatch(r"\$6\$[A-Za-z0-9./]{1,16}\$[A-Za-z0-9./]{86}", hashed):
            raise ValueError("could not generate a SHA-512 user password hash")
    return f"password_hash={hashed}\nssh_password_login={'yes' if password_login else 'no'}\n"


def studio_url(url):
    """The Studio address: https://, or http:// only to a private-network host (the lab)."""
    url = url.strip().rstrip("/")
    parts = urllib.parse.urlsplit(url)
    if len(url) > 200 or parts.query or parts.fragment or parts.username or not parts.hostname \
            or not re.fullmatch(r"https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?", url):
        raise ValueError("Studio address must look like https://studio.example.com")
    if parts.scheme == "http":
        host = parts.hostname
        try:
            private = ipaddress.ip_address(host).is_private
        except ValueError:
            private = host.endswith(".local") or "." not in host
        if not private:
            raise ValueError("plain http:// is only for a Studio on the local network; use https://")
    return url


def claim_code(secret):
    """Same derivation as beitlab-link and the Studio's device-hub (test vectors in scripts/tests)."""
    digest = hashlib.sha256(b"beitlab-claim-v1"
                            + bytes.fromhex(hashlib.sha256(secret.encode("ascii")).hexdigest())).digest()
    bits = int.from_bytes(digest[:4], "big") >> 2
    return "".join(CROCKFORD[(bits >> shift) & 31] for shift in range(25, -1, -5))


def studio_settings(url, secret=None):
    """Boot-volume files for the Studio link, and the claim code for the board card."""
    secret = secret or secrets.token_hex(32)
    if not re.fullmatch(r"[0-9a-f]{64}", secret):
        raise ValueError("device secret must be 64 lowercase hex characters")
    return {"studio.conf": studio_url(url) + "\n", "device-secret": secret + "\n"}, claim_code(secret)


def default_studio_url(root=None):
    try:
        data = json.loads((Path(root or Path(__file__).resolve().parents[1]) / "imager.json").read_text())
    except (OSError, ValueError):
        return None
    return data.get("studio_url") or None


def board_card(hostname, board, code, url):
    return (f"Hostname:    {hostname}\nBoard type:  {BOARD_LABEL.get(board, board or 'unknown')}\n"
            f"Claim code:  {code}\nStudio:      {url}\n"
            "Add the board in the Studio (My boards -> Add board) with the hostname and claim code.\n")


def image_board(image, offset):
    """The variant written on the boot volume (playground/board), or None for images before 0.5.0."""
    result = subprocess.run(["mcopy", "-n", "-i", f"{image}@@{offset}", "::/playground/board", "-"],
                            capture_output=True, text=True)
    value = result.stdout.strip() if result.returncode == 0 else ""
    return value if value in BOARD_LABEL else None


def provisioning_files(content=None, hostname=None, ssh_keys=None, user=None, studio=None):
    files = {}
    if content is not None:
        files["wifi.conf"] = content
    if hostname is not None:
        files["hostname"] = hostname_config(hostname)
    if ssh_keys is not None:
        files["authorized_keys"] = ssh_keys
        files["user.conf"] = user if user is not None else user_profile()
    elif user is not None:
        files["user.conf"] = user
    if studio is not None:
        if hostname is None:
            raise ValueError("the Studio link needs a hostname (students add the board by its hostname)")
        files.update(studio)
    if not files:
        raise ValueError("choose Wi-Fi, a hostname or an SSH public key")
    return files


def required_markers(files):
    """The newest marker the files need. Images are cumulative (a 0.5.0 card has every older marker),
    so as before only that one is checked."""
    order = ["wifi-supported", "setup-supported", "user-supported", "studio-supported"]
    return [max({MARKERS[name] for name in files}, key=order.index)]


def configure_boot(directory, content=None, hostname=None, ssh_keys=None, user=None, studio=None, board=None):
    files = provisioning_files(content, hostname, ssh_keys, user, studio)
    for marker in required_markers(files):
        if not (directory / "playground" / marker).is_file():
            raise ValueError(f"this boot volume lacks the requested provisioning support; use OS {MARKER_VERSION[marker]} or later")
    if board is not None:
        try:
            found = (directory / "playground" / "board").read_text().strip()
        except OSError:
            found = None
        if found != board:
            raise ValueError(f"this card has the {BOARD_LABEL.get(found, 'unknown')} on it, not the {BOARD_LABEL[board]}")
    for name in files:
        if os.path.lexists(directory / "playground" / name):
            raise FileExistsError(f"{directory / 'playground' / name} already exists")
    created = []
    try:
        for name, value in files.items():
            path = directory / "playground" / name
            private_write(path, value)
            created.append(path)
    except BaseException:
        for path in created:
            path.unlink()
        raise


def configure_image(source, destination, content=None, hostname=None, ssh_keys=None, user=None,
                    studio=None, board=None):
    files = provisioning_files(content, hostname, ssh_keys, user, studio)
    offset = boot_offset(source)
    for tool in ("mcopy", "mdir"):
        if not shutil.which(tool):
            raise ValueError("install mtools on the PC first (mcopy and mdir are required)")
    # A 0.1.x image lacks the first-boot importer. Refuse before making a
    # misleading personalized copy. This marker is added by the image build.
    source_drive = f"{source.resolve()}@@{offset}"
    for marker in required_markers(files):
        try:
            subprocess.run(["mdir", "-i", source_drive, f"::/playground/{marker}"],
                           check=True, capture_output=True)
        except subprocess.CalledProcessError as error:
            raise ValueError(f"image lacks the requested provisioning support; use OS {MARKER_VERSION[marker]} or later") from error
    if board is not None:
        found = image_board(source.resolve(), offset)
        if found != board:
            raise ValueError(f"this is the {BOARD_LABEL.get(found, 'pre-0.5.0')} image, not the {BOARD_LABEL[board]} "
                             "image; pick the image for your board")
    checksum_path = destination.with_name(destination.name + ".sha256")
    if checksum_path.exists():
        raise FileExistsError(f"{checksum_path} already exists")
    with tempfile.TemporaryDirectory(prefix="beitlab-wifi-") as temporary:
        for name, value in files.items():
            private_write(Path(temporary) / name, value)
        created = False
        try:
            with source.open("rb") as original, destination.open("xb") as output:
                created = True
                os.chmod(destination, 0o600)
                shutil.copyfileobj(original, output, 4 * 1024 * 1024)
            drive = f"{destination.resolve()}@@{offset}"
            for name in files:
                subprocess.run(["mcopy", "-o", "-i", drive, str(Path(temporary) / name),
                                f"::/playground/{name}"], check=True, capture_output=True)
            with destination.open("r+b") as output:
                os.fsync(output.fileno())
                digest = hashlib.sha256()
                for chunk in iter(lambda: output.read(4 * 1024 * 1024), b""):
                    digest.update(chunk)
            name = destination.name.replace("\\", "\\\\").replace("\n", "\\n")
            prefix = "\\" if name != destination.name else ""
            private_write(checksum_path, f"{prefix}{digest.hexdigest()}  {name}\n")
        except BaseException:
            if created:
                destination.unlink()
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--image", type=Path, help="uncompressed OS image; original is kept")
    target.add_argument("--boot-dir", type=Path, help="mounted boot volume, after flashing")
    parser.add_argument("--output", type=Path, help="new personal .img path (required with --image)")
    parser.add_argument("--ssid", help="network name; prompted if omitted")
    parser.add_argument("--country", help="two-letter Wi-Fi country code, e.g. CO")
    parser.add_argument("--hostname", help="board name, e.g. robot-01 (without .local)")
    parser.add_argument("--ssh-key", type=Path, action="append", default=[],
                        help="OpenSSH public key file (.pub); repeat to add more keys")
    parser.add_argument("--no-wifi", action="store_true", help="configure only hostname/SSH keys; do not ask for Wi-Fi")
    parser.add_argument("--user-password", action="store_true", help="prompt for a unique beitlab password for sudo/local login")
    parser.add_argument("--ssh-password-login", action="store_true", help="explicitly enable password SSH (requires --user-password)")
    parser.add_argument("--board", choices=sorted(BOARDS), help="the board this card is for; refuses an image of the other variant")
    studio = parser.add_mutually_exclusive_group()
    studio.add_argument("--studio-url", help="Studio address the board links to, e.g. https://studio.beitlab.com")
    studio.add_argument("--no-studio", action="store_true", help="do not link this board to a Studio")
    args = parser.parse_args()
    if bool(args.image) != bool(args.output):
        parser.error("--image requires --output; --boot-dir does not use --output")
    if args.no_wifi and (args.ssid is not None or args.country is not None):
        parser.error("--no-wifi cannot be combined with --ssid or --country")
    if args.ssh_password_login and not args.user_password:
        parser.error("--ssh-password-login requires --user-password")
    try:
        if args.hostname is not None:
            hostname_config(args.hostname)
        keys = public_keys(args.ssh_key) if args.ssh_key else None
        user = None
        if args.user_password:
            password = getpass.getpass("New beitlab password (hidden, at least 12 characters): ")
            if password != getpass.getpass("Confirm beitlab password: "):
                raise ValueError("user passwords do not match")
            user = user_profile(password, args.ssh_password_login)
            del password
        studio_files, code, url = None, None, None
        if args.studio_url and not args.no_studio:  # opt-in here; the guided imager offers imager.json's default
            url = studio_url(args.studio_url)
            if args.hostname is None:
                raise ValueError("the Studio link needs --hostname (for example robot-07), or use --no-studio")
            studio_files, code = studio_settings(url)
        content = None
        if not args.no_wifi:
            ssid = args.ssid if args.ssid is not None else input("Wi-Fi SSID: ")
            password = getpass.getpass("Wi-Fi password (hidden): ")
            content = profile(ssid, password, args.country)
            del password
        board = BOARDS[args.board] if args.board else None
        if args.image:
            configure_image(args.image, args.output, content, args.hostname, keys, user, studio_files, board)
            print(f"First-boot settings configured. Flash {args.output} with your image writer.")
        else:
            configure_boot(args.boot_dir, content, args.hostname, keys, user, studio_files, board)
            print("First-boot settings configured. Safely eject the card and boot the Duo S.")
        if studio_files:
            print("\n== Board card (write it down or print it) ==\n"
                  + board_card(args.hostname.lower(), board, code, url))
        if args.hostname:
            print(f"Connect after boot: ssh beitlab@{args.hostname.lower()}.local")
        if content or user or studio_files:
            print("Keep this image/card private: it contains network, account or Studio credentials.")
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        # mtools arguments contain paths only, never the password/PSK.
        parser.exit(1, f"configure-wifi: {error}\n")


if __name__ == "__main__":
    main()
