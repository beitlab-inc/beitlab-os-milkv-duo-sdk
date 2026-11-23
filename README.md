# beitlab Robotics MilkV duo OS

A flashable Linux image for the Milk-V Duo S, plus PlatformIO examples that load a program onto the C906 real-time core from another computer.

Preconfigure Wi-Fi if needed, flash the microSD card, and set the Duo S boot switch to ARM. Ethernet also works without configuration. The J22 serial Wi-Fi wizard is also available. The board boots Linux with the agent installed and enabled from the first boot, before network setup. PlatformIO on a PC cross-compiles a C906 ELF, and the agent restarts only that core through the kernel `remoteproc` class. Linux and your SSH session stay up.

The robot control firmware (`Balanced_robot`) and the carrier board design (`Robotics_board`) are separate repositories that are not published here. This project does not build either of those; their pin data needed by the examples is copied into `profiles/`.

## Start here

- Run **🤖 beitlab Imager** with `python3 scripts/beitlab-imager.py --image /path/to/your.img` for guided Wi-Fi setup and card flashing. It checks checksums, hides passwords, discovers removable drives and verifies the write. Requires Python 3.9+ and `mtools`; native Linux/macOS.
- Use local images with `--image` during development. Once the configured repository is public and has a published image release, clients can run `python3 scripts/beitlab-imager.py` to download, verify, configure and flash the latest release. Public downloads need no GitHub account or GitHub CLI. See [FLASH.md](docs/FLASH.md) for version selection and optional private-repository access.
- To burn an image you already prepared, run `python3 scripts/beitlab-imager.py --flash-only --image "$HOME/Downloads/my-duo.img"`. It keeps the saved settings and asks only for card selection and erase confirmation.
- [docs/FLASH.md](docs/FLASH.md) takes you from a blank card to the `hello` banner.
- [docs/sdk-notes.md](docs/sdk-notes.md) records what the pinned SDK contains, the decisions taken, and the acceptance-test results.
- [docs/PROFILES.md](docs/PROFILES.md) selects bare-Duo or robotics-carrier pin permissions in PlatformIO.
- [docs/IMU.md](docs/IMU.md) runs the carrier BMI323 example over I²C and prints acceleration, angular rate and temperature to J22 or the SSH monitor.
- [docs/CI.md](docs/CI.md) describes external build paths and GitHub Actions release assets.
- [PROMPT.md](PROMPT.md) is the implementation contract.
- Source in this repository is MIT-licensed ([LICENSE](LICENSE)) unless a file's `SPDX-License-Identifier` says otherwise: `image/dts/playground_duos.dtsi` and `agent/vendor_abi.h` are GPL-2.0, and the SDK patches in `image/patches/` carry the license of the upstream files they modify (GPL-2.0 for Linux).
- [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES) credits the upstream projects; [docs/LICENSING.md](docs/LICENSING.md) records the license review and remaining redistribution requirements.

## Status

| Part | State |
| --- | --- |
| SDK pin (`sdk.lock`), patches, DT fragment, overlay, build scripts | written; the patches apply to the pinned commit; the DT passes the carrier check |
| `rtos-agent` | written; host tests pass |
| Starter firmware and the three examples | starter linked with the SDK toolchain and in the image; the examples type-check against the vendor headers and build the same way |
| SD image | **built**: `beitlab-robotics-milkv-duo-os-0.4.1-duos-arm64-sd.img` (local release output); Wi-Fi, hostname and SSH public-key configuration before flashing; all post-build checks pass |
| Wi-Fi 0.2.0 verification | automated provisioning, FAT image, boot-import and driver-loader checks pass; fresh-card association/DHCP/reconnect pending (no board connected on 2026-11-20) |
| First-boot customization 0.4.0 | combined Wi-Fi/hostname/key tests pass on real FAT images and isolated BusyBox imports; fresh-card SSH/mDNS verification pending |
| Studio link 0.5.0 | source written: `beitlab-link`, `import-studio`, board-status decoder, Python 3 (patch 0010), two image variants (`make-variant.py`), imager board choice, Studio address, claim code; host tests pass and the link runs against the Studio's mock board. **Image not built yet; not verified on hardware.** The Robotics Board inspector firmware is not written yet |
| Acceptance tests (0.1.0) | on a Duo S, 2026-11-19: tests 2-8 passed, with uploads and recover leaving Linux running (same `boot_id`); test 1 passed over Wi-Fi, but the Ethernet link is not recorded |

## Layout

```
sdk.lock               pinned SDK commit, target, toolchain hashes
VERSION                image version
image/patches/         SDK patches (remoteproc driver, kernel/Buildroot config, DT include)
image/dts/             playground_duos.dtsi: C906L remoteproc node, carrier pads released
image/overlay/         rootfs overlay: systemd units, sshd_config, avahi, lock file
agent/                 rtos-agent (C, static) and its tests: make -C agent test
firmware/include/      playground_cmd.h (mailbox ids), playground.h (runtime API)
firmware/runtime/      C906 runtime: mailbox, log, main_cvirtos()
firmware/starter/      starter program baked into the image
firmware/rtosbuild.py  builds any playground ELF with the SDK's own flags
profiles/              hardware pin permissions; selected by custom_profile in platformio.ini
examples/platformio/   bare-Duo examples, robotics_board, robotics_imu, common/ (upload, monitor, platform)
scripts/               configure-image.py, configure-wifi.py, fetch-freertos.sh, build-rtos-libs.sh, build-image.sh,
                       check-dtb.py, flash.sh,
                       acceptance.py (tests 1-7 over SSH, PC side)
docs/                  FLASH.md, sdk-notes.md
.github/workflows/     source tests, image build, draft Release assets
SDK/images/builds      external cache only; see docs/CI.md
```

## Intended use

1. Download the image from GitHub Releases and flash it ([docs/FLASH.md](docs/FLASH.md)).
2. Boot the Duo S with preconfigured Wi-Fi or Ethernet, or answer the Wi-Fi prompt on J22 (115200 8N1). It appears as `beitlab-milkv-duo.local`.
3. On the PC, run `scripts/fetch-freertos.sh` once. Then open `examples/platformio/hello` and run `pio run -t upload`.
4. The C906 prints the example banner. Watch it with `pio run -t rtmonitor`. Linux stays up.

The existing Duo examples explicitly select `custom_profile = duos_bare` in
their own `platformio.ini`. For the Beitlab robotics carrier revision 0.11,
open [examples/platformio/robotics_board](examples/platformio/robotics_board/README.md),
which selects `beitlab_robotics_rev011`, prints carrier reservations, and
blinks the onboard LED. See [the profile table](docs/PROFILES.md) for the
profile and purpose of each example.

If both network links are disconnected, the serial wizard asks whether to connect Wi-Fi, accepts an SSID and hidden password, retries three times, and lets you change credentials or cancel. A successful connection is saved for later boots. Run `sudo beitlab-wifi-setup` to reopen it. The default SSH account is `beitlab`, authenticated by a provisioned public key. Root SSH is disabled and there is no shared password; firmware filenames and `/etc/duo-playground-release` retain their existing interfaces.

To configure Wi-Fi, a hostname and an SSH public key before flashing, install Python 3 and `mtools` on your PC, then run:

```sh
python3 scripts/configure-image.py --image $HOME/Downloads/beitlab-robotics-milkv-duo-os-0.4.1-duos-arm64-sd.img \
  --output $HOME/Downloads/my-duo.img --ssid "Your network" --country CO \
  --hostname robot-01 --ssh-key ~/.ssh/id_ed25519.pub --user-password
```

Choose a unique administration password and enter the Wi-Fi password at the hidden prompts, then flash `$HOME/Downloads/my-duo.img` and connect with `ssh beitlab@robot-01.local`. Set `PIO_DUO_HOST=robot-01.local` for PlatformIO. Use your country's two-letter code; hostname/key options are optional. Add `--no-wifi` for Ethernet-only configuration. The original release image stays unchanged; the personal copy contains a network secret. See [docs/FLASH.md](docs/FLASH.md) for the mounted-card alternative and first-boot checks.

OS 0.4.0 uses SSH keys by default. `--user-password` sets the regular user’s sudo/local password; it does not enable password SSH. Add `--ssh-password-login` explicitly if needed. PlatformIO uses a restricted sudo helper; connect once by SSH to verify the host key before uploading. See [docs/SECURITY.md](docs/SECURITY.md).

OS 0.4.1 includes `bl_send`. Mailbox tools run directly as `beitlab`,
using group access to `/dev/cvi-rtos-cmdqu`; no sudo password is needed
for mailbox commands. General administration still requires sudo.
