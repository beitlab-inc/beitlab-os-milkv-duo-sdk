#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""🤖 beitlab Imager — choose, configure, flash and verify a Duo S card."""
import argparse
import getpass
import os
from pathlib import Path
import runpy
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import imager_drives as drives
import imager_sources as sources

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()


class WorkerCancelled(Exception):
    """The child already displayed its cancellation message."""


def run_worker(argv):
    # The foreground child receives Ctrl-C too. Keep waiting for its status
    # so it can report whether writing had started, without a second message
    # from the parent. A caught handler resets on exec; SIG_IGN would instead
    # be inherited by the child and prevent normal Ctrl-C handling there.
    previous = signal.signal(signal.SIGINT, lambda *_: None)
    try:
        result = subprocess.run(argv)
    finally:
        signal.signal(signal.SIGINT, previous)
    if result.returncode in (130, -2):
        raise WorkerCancelled
    if result.returncode:
        raise ValueError("the disk writer did not complete; check the message above")


def plain(value):
    # Device model names and release data must not inject terminal escapes.
    return "".join(c if c.isprintable() else " " for c in str(value))


class UI:
    def __init__(self, color=True, emoji=True):
        self.color = color and sys.stdout.isatty() and "NO_COLOR" not in os.environ
        self.emoji = emoji
        try:
            "🤖".encode(sys.stdout.encoding or "utf-8")
        except UnicodeEncodeError:
            self.emoji = False
        self.last_progress = 0
        self.progress_line = False

    def finish_line(self):
        if self.progress_line:
            print()
            self.progress_line = False

    def say(self, message, icon=""):
        self.finish_line()
        prefix = icon + " " if self.emoji and icon else ""
        print(prefix + plain(message), flush=True)

    def banner(self, flash_only=False):
        title = "beitlab  /  ROBOTICS"
        if self.color:
            title = "\033[1;36m" + title + "\033[0m"
        print("\n" + title)
        self.say("MilkV duo OS · Card Imager", "🤖")
        setup = "Use saved settings" if flash_only else "Set up board"
        self.say(f"Choose image  →  {setup}  →  Select card  →  Flash & verify")
        self.say("Ctrl-C cancels at any step.")

    def step(self, number, title, icon):
        self.finish_line()
        print()
        self.say(f"{number}/4  {title}", icon)

    def ask(self, message, default="", strip=True):
        self.finish_line()
        suffix = f" [{default}]" if default not in (None, "") else ""
        answer = input(plain(message) + suffix + ": ")
        if strip:
            answer = answer.strip()
        return answer or (default if default is not None else "")

    def yes(self, message, default=True):
        while True:
            value = self.ask(message + (" [Y/n]" if default else " [y/N]"), "").lower()
            if not value:
                return default
            if value in ("y", "yes"):
                return True
            if value in ("n", "no"):
                return False
            self.say("Enter y or n.")

    def password(self, label, check, confirm=True):
        self.finish_line()
        while True:
            # Refuse getpass's echoing fallback, including piped input.
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                value = getpass.getpass(label + " (hidden): ")
                if confirm and value != getpass.getpass("Confirm password (hidden): "):
                    self.say("Passwords do not match. Try again.")
                    continue
            try:
                return check(value)
            except ValueError as error:
                self.say(error, "⚠️")
            finally:
                del value

    def progress(self, label, done, total):
        now = time.monotonic()
        if now - self.last_progress < 0.15 and done != total:
            return
        self.last_progress = now
        if total:
            percent = min(100, int(done * 100 / total))
            bar = "#" * (percent // 5) + "-" * (20 - percent // 5)
            text = f"{label:16} [{bar}] {percent:3d}%  {done / 1024**2:.0f} MiB"
        else:
            text = f"{label:16} {done / 1024**2:.0f} MiB"
        if sys.stdout.isatty():
            print("\r" + text + "   ", end="", flush=True)
            self.progress_line = True
            if total and done == total:
                self.finish_line()


def image_source(args, ui, temporary):
    ui.step(1, "Choose your OS image", "📦")
    if args.image:
        image = args.image.expanduser().resolve(strict=True)
        expected = args.sha256 or sources.adjacent_checksum(image)
    else:
        repository = sources.resolve_repository(args.github_repo, ROOT)
        if repository:
            ui.say(f"Getting {repository} · {args.release} from GitHub Releases", "⬇️")
            if args.github_auth:
                ui.say("Using your GitHub CLI login for this download.")
            image, expected = sources.github_image(repository, args.release, args.cache_dir, ui.progress,
                                                   authenticated=args.github_auth)
        else:
            work = Path(os.environ.get("BEITLAB_WORK_DIR", str(Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "beitlab-duo-os")))
            local = sorted((work / "releases").glob("*/beitlab-robotics-milkv-duo-os-*-duos-arm64-sd.img*"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            default = str(local[0]) if local else ""
            image = Path(ui.ask("Local image path (.img / .img.xz / .img.gz)", default)).expanduser().resolve(strict=True)
            expected = args.sha256 or sources.adjacent_checksum(image)
    ui.say(f"Image: {image.name}")
    if expected is None:
        ui.say("No matching checksum found beside the image. Use its release SHA256.")
        expected = ui.ask("SHA256 checksum", default=None)
    expected = sources.valid_digest(expected)
    if sources.digest_file(image, ui.progress) != expected:
        raise ValueError("image checksum failed; no drive was written")
    ui.say("Image checksum verified.", "✅")
    return sources.unpack(image, temporary, ui.progress), image


def capabilities(image, config):
    offset = config["boot_offset"](image)
    found = set()
    for name in ("wifi-supported", "setup-supported", "user-supported"):
        result = subprocess.run(["mdir", "-i", f"{image}@@{offset}", f"::/playground/{name}"],
                                capture_output=True)
        if result.returncode == 0:
            found.add(name)
    return found


def settings(ui, config, features):
    ui.step(2, "Make it your board", "⚙️")
    wifi = None
    ssid = None
    if "wifi-supported" in features and ui.yes("Configure Wi-Fi before first boot?"):
        while True:
            ssid = ui.ask("Wi-Fi SSID", default=None, strip=False)
            try:
                # Validate SSID before asking for a password.
                config["profile"](ssid, "validation-only")
                break
            except ValueError as error:
                ui.say(error, "⚠️")
        while True:
            country = ui.ask("Wi-Fi country (two letters; Enter to leave unset)").upper() or None
            try:
                config["profile"](ssid, "validation-only", country)
                break
            except ValueError as error:
                ui.say(error, "⚠️")
        wifi = ui.password("Wi-Fi password", lambda value: config["profile"](ssid, value, country))
    elif "wifi-supported" not in features:
        ui.say("This older image has no boot Wi-Fi importer; use a newer image for Wi-Fi setup.")
    hostname = None
    if "setup-supported" in features:
        while True:
            hostname = ui.ask("Hostname (without .local)", "beitlab-milkv-duo")
            try:
                hostname = config["hostname_config"](hostname).strip()
                break
            except ValueError as error:
                ui.say(error, "⚠️")
    keys = None
    user = None
    account = "beitlab" if "user-supported" in features else "root"
    if "user-supported" in features:
        ui.say("SSH access for the beitlab account:")
        ui.say("  1  SSH public key (.pub)    2  Unique SSH password")
        while True:
            mode = ui.ask("Choose access method", "1")
            if mode == "1":
                while True:
                    answer = ui.ask("Public key file (.pub), or b to change access method", "~/.ssh/id_ed25519.pub")
                    if answer.lower() == "b":
                        break
                    key = Path(answer).expanduser()
                    try:
                        keys = config["public_keys"]([key])
                        break
                    except (OSError, ValueError) as error:
                        ui.say(error, "⚠️")
                if keys is not None:
                    break
                continue
            if mode == "2":
                if not shutil.which("openssl"):
                    raise ValueError("password provisioning needs openssl on the PC; install it or use a public key")
                user = ui.password("New beitlab password (at least 12 characters)",
                                   lambda value: config["user_profile"](value, password_login=True))
                break
            ui.say("Choose 1 or 2.")
    else:
        ui.say("This image uses its existing login settings. Regular-user key/password setup needs OS 0.4.0.")
    return wifi, hostname, keys, user, ssid, account


def choose_drive(ui, paths, size):
    ui.step(3, "Choose the microSD card", "💾")
    while True:
        all_drives = drives.scan(paths)
        eligible = [d for d in all_drives if d.eligible and d.size >= size]
        if eligible:
            for index, drive in enumerate(eligible, 1):
                ui.say(f"  {index}  {drive.path} · {drive.model} · {drive.size / 1_000_000_000:.1f} GB")
                if drive.mounts:
                    ui.say("       Mounted: " + ", ".join(drive.mounts))
        else:
            ui.say("No writable removable drive large enough for this image was found.")
        ui.say("  r  Refresh after inserting a card    q  Cancel")
        answer = ui.ask("Drive number / r / q", default=None).lower()
        if answer == "q":
            raise KeyboardInterrupt
        if answer == "r":
            continue
        if answer.isdecimal() and 1 <= int(answer) <= len(eligible):
            return eligible[int(answer) - 1]
        ui.say("Choose a listed drive number, r, or q.")


def execute(args, ui):
    ui.banner(args.flash_only)
    if args.list_drives:
        inventory = drives.scan()
        for drive in inventory:
            ui.say(f"{drive.path} · {drive.model} · {drive.size / 1_000_000_000:.1f} GB · "
                   + ("available" if drive.eligible else "blocked: " + drive.reason))
        if not any(d.eligible for d in inventory):
            ui.say("No eligible removable drives. Insert a card and run again.")
        return
    if not sys.stdin.isatty():
        raise ValueError("the wizard requires an interactive terminal; use --list-drives for read-only discovery")
    if not (sys.platform.startswith("linux") or sys.platform == "darwin"):
        raise ValueError("This version flashes on Linux/macOS. Native Windows flashing is not implemented.")
    if not args.flash_only and (not shutil.which("mcopy") or not shutil.which("mdir")):
        raise ValueError("Install mtools first: sudo apt install mtools (Linux), or brew install mtools (macOS)")
    if args.prepare_only and not args.output:
        raise ValueError("--prepare-only requires --output for the personal image")
    if args.output and (args.output.exists() or args.output.with_name(args.output.name + ".sha256").exists()):
        raise FileExistsError("the personal output image or its checksum already exists")
    config = runpy.run_path(str(ROOT / "scripts/configure-wifi.py"))
    with tempfile.TemporaryDirectory(prefix="beitlab-imager-") as folder:
        temporary = Path(folder)
        image, original = image_source(args, ui, temporary)
        if args.flash_only:
            config["boot_offset"](image)  # Validate the Duo S image without mtools or mounting.
            ui.step(2, "Use saved board settings", "⚙️")
            ui.say("Using the Wi-Fi and account settings already saved in this image.")
        else:
            features = capabilities(image, config)
            wifi, hostname, keys, user, ssid, account = settings(ui, config, features)
            target = args.output.expanduser().resolve() if args.output else temporary / "personal.img"
            if wifi is not None or hostname is not None or keys is not None or user is not None:
                ui.say("Preparing your first-boot settings…", "🛠️")
                config["configure_image"](image, target, wifi, hostname, keys, user)
                image = target
            elif args.output:
                with image.open("rb") as source, target.open("xb") as output:
                    os.chmod(target, 0o600)
                    shutil.copyfileobj(source, output, sources.CHUNK)
                digest = sources.digest_file(target, ui.progress)
                config["private_write"](target.with_name(target.name + ".sha256"), f"{digest}  {target.name}\n")
                image = target
            if args.output:
                ui.say(f"Personal image saved: {image}")
            if args.prepare_only or args.dry_run:
                ui.say("Preparation complete. No disk was opened for writing.", "✅")
                return
        drive = choose_drive(ui, [image, original], image.stat().st_size)
        ui.step(4, "Review & flash", "🚀")
        ui.say(f"Image: {original.name}")
        if args.flash_only:
            ui.say("Board settings: saved in the prepared image.")
        else:
            ui.say(f"Hostname: {hostname or 'image default'} · Wi-Fi: {ssid or 'image default / Ethernet'}")
        ui.say(f"Target: {drive.path} · {drive.model} · {drive.size / 1_000_000_000:.1f} GB")
        expected = sources.digest_file(image, ui.progress)
        if os.geteuid() == 0:
            drives.flash(image, drive.path, drive.identity, expected, ui)
        else:
            ui.say("Administrator access is needed only for the disk-writing step.")
            if not shutil.which("sudo"):
                raise ValueError("sudo is unavailable; run this trusted CLI as an administrator")
            run_worker(["sudo", "--", sys.executable, str(SCRIPT), "--_flash-worker",
                            str(image), drive.path, expected, drive.identity,
                            *(["--no-emoji"] if not ui.emoji else []),
                            *(["--no-color"] if not ui.color else [])])
        ui.say("Set the Duo S switch to ARM before power-on.", "🔌")
        if args.flash_only:
            ui.say("After boot, connect using the hostname and login saved when you prepared this image.", "✨")
        else:
            ui.say(f"After boot: ssh {account}@{hostname or 'beitlab-milkv-duo'}.local", "✨")
        ui.say("On the Beitlab carrier, power the carrier and leave USB-C disconnected.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, help="local image; takes priority over GitHub")
    parser.add_argument("--sha256", help="local image checksum when no matching sidecar exists")
    parser.add_argument("--github-repo", help="release repository: OWNER/REPO")
    parser.add_argument("--github-auth", action="store_true", help="use your GitHub CLI login for private/draft release downloads")
    parser.add_argument("--release", default="latest", help="GitHub release tag (default: latest)")
    parser.add_argument("--cache-dir", type=Path, default=Path.home() / ".cache/beitlab-imager")
    parser.add_argument("--output", type=Path, help="keep a personal image instead of only a temporary copy")
    parser.add_argument("--prepare-only", action="store_true", help="save the configured image without flashing (requires --output)")
    parser.add_argument("--flash-only", action="store_true", help="burn an existing prepared --image without changing its settings")
    parser.add_argument("--dry-run", action="store_true", help="run image setup without selecting, unmounting or writing a disk")
    parser.add_argument("--list-drives", action="store_true", help="read-only drive discovery; no image or mtools needed")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--no-emoji", action="store_true")
    parser.add_argument("--_flash-worker", nargs=4, metavar=("IMAGE", "DRIVE", "SHA256", "IDENTITY"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.flash_only:
        if args.image is None:
            parser.error("--flash-only requires --image pointing to your prepared image")
        if args.output or args.prepare_only or args.dry_run or args.list_drives:
            parser.error("--flash-only cannot be combined with --output, --prepare-only, --dry-run or --list-drives")
    ui = UI(not args.no_color, not args.no_emoji)
    writing = bool(args._flash_worker)
    try:
        if args._flash_worker:
            image, path, checksum, identity = args._flash_worker
            drives.flash(Path(image), path, identity, sources.valid_digest(checksum), ui)
        else:
            execute(args, ui)
    except WorkerCancelled:
        return 130
    except drives.FlashCancelled as error:
        if error.writing_started:
            ui.say("Cancelled during flashing. Flash the card again before booting it.", "🛑")
        else:
            ui.say("Cancelled before writing. The drive was not changed.", "🛑")
        return 130
    except (KeyboardInterrupt, EOFError):
        ui.say("Cancelled before flashing. No drive was written.", "🛑")
        return 130
    except (OSError, ValueError, subprocess.CalledProcessError, getpass.GetPassWarning) as error:
        ui.say(error, "❌")
        if writing:
            ui.say("Flashing did not complete. Flash again before using the card.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
