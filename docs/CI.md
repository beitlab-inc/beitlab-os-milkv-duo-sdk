# Source checkout, builds and release assets

The Git repository contains source, configuration, patches, tests and
required license notices. It contains no SD images, fetched SDK, binaries,
toolchains or `out/` directory.

## Build locations

Local builds default to `~/.cache/beitlab-duo-os/` (or
`$XDG_CACHE_HOME/beitlab-duo-os/`):

| Location | Contents |
| --- | --- |
| `sdk/` | Fetched SDK and toolchains at `sdk.lock`; sparse FreeRTOS checkout for example users |
| `build/` | Agent and starter build products |
| `releases/<VERSION>/` | Complete SD image, agent binary, checksums and build checks |
| `reports/` | Hardware acceptance logs |

Override `BEITLAB_WORK_DIR` to move all of these, `PLAYGROUND_SDK` for the
SDK alone, `BEITLAB_BUILD_DIR` for compiler outputs, or `BEITLAB_OUTPUT_DIR`
for release outputs. The Docker build mounts these external directories;
it does not require a terminal or create `sdk/`/`out/` in the checkout.

```sh
scripts/build-image.sh --docker
python3 scripts/package-release.py \
  --build-dir "$HOME/.cache/beitlab-duo-os/releases/$(cat VERSION)" \
  --asset-dir "$HOME/.cache/beitlab-duo-os/assets/$(cat VERSION)"
```

PlatformIO's own `.pio/` directories are ignored build caches. They are
created only when compiling examples, not checked into Git. Image builds
and host agent builds use the external build directory by default.

## GitHub Actions

[The image workflow](../.github/workflows/image.yml) runs source, imager,
account, GPIO policy and real FAT filesystem tests on pushes to `main`,
pull requests, manual runs and `v*` tags. A manual run or version tag also
builds the image with the locked SDK in its supported Docker environment.
The version tag must equal `v` plus the contents of `VERSION`.

The image job first removes unused preinstalled tools on disposable
GitHub-hosted VMs, then requires at least 45 GiB free. Standard runner
storage is not guaranteed to satisfy a full SDK build. If the preflight
fails, set repository Actions variable `IMAGE_BUILD_RUNNER` to a configured
larger or self-hosted Linux x64 runner label. A self-hosted runner needs
Docker, Git, Python 3.10+, xz, GitHub's runner prerequisites and sufficient
storage. The workflow never removes installed tools from self-hosted
machines. Do not register an untrusted public-fork runner for the image
job; only manual/version-tag runs execute that job.

Every completed build checks the kernel/DT remoteproc path, carrier pin
reservations (using the in-repo profile snapshot without sibling projects),
rootfs identity, regular account, SSH/sudo permissions, password tools,
Wi-Fi payload and boot provisioning markers. The source checkout remains
free of SDK and image artifacts. The build job has read-only repository
permission; only the separate release job has `contents: write`.

Release assets are:

- the compressed `.img.xz` and a checksum sidecar for its extracted `.img`;
- the agent binary;
- a ZIP with the imager CLI, provisioning helpers and instructions;
- selected upstream license notices;
- build/DT check reports and `SHA256SUMS` for every attached file.

The workflow verifies all asset hashes and attaches them to a **draft
GitHub Release**. It refuses to overwrite a published release or a draft
for a different source commit. Review the draft's hardware-test status
and the outstanding requirements in [LICENSING.md](LICENSING.md) before
publishing. Build diagnostics are also retained as Actions artifacts for
14 days. The automatically provided GitHub source archives contain only
the clean source tree, not the downloaded SDK or the complete
corresponding-source delivery required for every bundled component.

Run the workflow from Actions → **Build OS image** → **Run workflow**, or:

```sh
gh workflow run image.yml --repo beitlab-inc/beitlab-os-milkv-duo-sdk
```

During development, use a local image with
`python3 scripts/beitlab-imager.py --image /path/to/image.img`.
The local 0.4.1 build is independent of GitHub release publication.

For future client distribution, make the release repository public,
configure its `OWNER/REPO` in the bundled `imager.json`, and publish a
reviewed release with the generated assets. Clients then run
`python3 scripts/beitlab-imager.py` to obtain the latest published image,
verify it, configure their board and flash the card. Public downloads
need no GitHub login, account or GitHub CLI. An explicit `--release` can
select a particular published version.

Private or draft release downloads remain an optional developer workflow:
use `--github-auth` after `gh auth login --hostname github.com`, with an
explicit `--release` tag for a draft. The configured repository is
currently `beitlab-inc/beitlab-os-milkv-duo-sdk`; change the bundle setting
if the future public release repository differs. See [FLASH.md](FLASH.md).

## Verified build

[Run 44821272091](https://github.com/beitlab-inc/beitlab-os-milkv-duo-sdk/actions/runs/44821272091)
completed the source tests, clean SDK image build, asset packaging and
draft release upload for version 0.4.0 at commit
`0f2fd639e071cf96452dbfb047756660bf152b4b` on 2026-11-20.
On 2026-11-21 the downloaded assets and extracted image passed their
checksums; the bundled imager also personalized that image while keeping
the release original and non-boot partitions unchanged. See
[sdk-notes.md](sdk-notes.md#github-actions-build-and-downloaded-assets-2026-11-21)
for the image hash and remaining hardware checks.

References: [GitHub runner specifications](https://docs.github.com/en/actions/reference/runners/github-hosted-runners),
[GitHub CLI releases](https://cli.github.com/manual/gh_release_create).
