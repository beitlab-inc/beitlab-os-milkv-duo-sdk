"""Local-image verification and future public GitHub release downloads."""
import gzip
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request

CHUNK = 4 * 1024 * 1024
MAX_IMAGE = 16 * 1024**3
ASSET = re.compile(r"beitlab-robotics-milkv-duo-os-[A-Za-z0-9._-]+-duos-arm64-sd\.img(?:\.xz|\.gz)?$")


def digest_file(path, progress=lambda *args: None):
    total = path.stat().st_size
    digest = hashlib.sha256()
    done = 0
    with path.open("rb") as image:
        for chunk in iter(lambda: image.read(CHUNK), b""):
            digest.update(chunk)
            done += len(chunk)
            progress("Checking image", done, total)
    return digest.hexdigest()


def valid_digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", value):
        raise ValueError("a complete 64-character SHA256 checksum is required")
    return value.lower()


def checksum_entry(text, name):
    for line in text.splitlines():
        match = re.fullmatch(r"([a-fA-F0-9]{64}) [ *](.+)", line)
        if match and match[2] in (name, "./" + name):
            return valid_digest(match[1])
    raise ValueError(f"checksum file has no entry for {name}")


def adjacent_checksum(image):
    for sums in (image.with_name(image.name + ".sha256"), image.parent / "SHA256SUMS"):
        if sums.is_file():
            try:
                return checksum_entry(sums.read_text(encoding="utf-8"), image.name)
            except ValueError:
                continue
    return None


def resolve_repository(explicit, root):
    repository = explicit or os.environ.get("BEITLAB_IMAGE_REPO")
    if not repository:
        settings = root / "imager.json"
        if settings.exists():
            repository = json.loads(settings.read_text()).get("github_repository")
    if not repository:
        remote = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"],
                                capture_output=True, text=True) if shutil.which("git") else None
        if remote and remote.returncode == 0:
            match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:)([\w.-]+/[\w.-]+?)(?:\.git)?", remote.stdout.strip())
            if match:
                repository = match[1]
    if repository and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("GitHub repository must be OWNER/REPO")
    return repository


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise ValueError("refusing a download redirect outside HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def https_open(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("release downloads require an HTTPS URL without embedded credentials")
    request = urllib.request.Request(url, headers={"User-Agent": "beitlab-imager/1.0", "Accept": "application/vnd.github+json" if parsed.hostname == "api.github.com" else "*/*"})
    return urllib.request.build_opener(HTTPSRedirect()).open(request, timeout=30)


def read_metadata(url, limit=1024*1024):
    with https_open(url) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("release metadata exceeds the size limit")
    return data


def release_asset(data):
    assets = [a for a in data.get("assets", []) if ASSET.fullmatch(a.get("name", "")) and a.get("state", "uploaded") == "uploaded"]
    if not assets:
        raise ValueError("the GitHub release has no compatible beitlab Duo S image asset")
    return min(assets, key=lambda a: (0 if a["name"].endswith(".xz") else 1 if a["name"].endswith(".gz") else 2, a["name"]))


def github_image(repository, tag, cache, progress):
    endpoint = "latest" if tag == "latest" else "tags/" + urllib.parse.quote(tag, safe="")
    release = json.loads(read_metadata(f"https://api.github.com/repos/{repository}/releases/{endpoint}"))
    asset = release_asset(release)
    value = asset.get("digest") or ""
    if value.startswith("sha256:"):
        expected = valid_digest(value[7:])
    else:
        sums = next((a for a in release.get("assets", []) if a["name"] == asset["name"] + ".sha256"), None)
        sums = sums or next((a for a in release.get("assets", []) if a["name"] == "SHA256SUMS"), None)
        if sums is None:
            raise ValueError("release has no SHA256 digest or checksum asset for its image")
        expected = checksum_entry(read_metadata(sums["browser_download_url"]).decode(), asset["name"])
    cache.mkdir(parents=True, exist_ok=True)
    filename = cache / (expected + "-" + asset["name"])
    if filename.exists():
        if digest_file(filename, progress) == expected:
            return filename, expected
        raise ValueError(f"cached image checksum failed; remove {filename} and retry")
    # Partial downloads are never mistaken for a verified cached image.
    with tempfile.NamedTemporaryFile(prefix=".download-", dir=cache, delete=False) as partial:
        temporary = Path(partial.name)
        try:
            with https_open(asset["browser_download_url"]) as response:
                declared = int(response.headers.get("Content-Length") or asset.get("size") or 0)
                if declared > MAX_IMAGE:
                    raise ValueError("download is too large for this Duo S imager")
                digest = hashlib.sha256()
                done = 0
                for chunk in iter(lambda: response.read(CHUNK), b""):
                    done += len(chunk)
                    if done > MAX_IMAGE:
                        raise ValueError("download exceeded the size limit")
                    partial.write(chunk)
                    digest.update(chunk)
                    progress("Downloading", done, declared)
                if digest.hexdigest() != expected:
                    raise ValueError("download checksum failed; no drive was written")
                partial.flush()
                os.fsync(partial.fileno())
            partial.close()
            temporary.rename(filename)
        finally:
            temporary.unlink(missing_ok=True)
    return filename, expected


def unpack(image, directory, progress):
    if image.suffix not in (".xz", ".gz"):
        if image.suffix != ".img":
            raise ValueError("choose an uncompressed .img, .img.xz or .img.gz file")
        return image
    opener = lzma.open if image.suffix == ".xz" else gzip.open
    destination = directory / "release.img"
    done = 0
    created = False
    try:
        with opener(image, "rb") as source, destination.open("xb") as output:
            created = True
            for chunk in iter(lambda: source.read(CHUNK), b""):
                done += len(chunk)
                if done > MAX_IMAGE:
                    raise ValueError("expanded image exceeds the size limit")
                output.write(chunk)
                progress("Unpacking", done, 0)
        return destination
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise
