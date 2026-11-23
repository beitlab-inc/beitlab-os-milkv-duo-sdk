# Implementation prompt: beitlab Robotics MilkV duo OS

The 2026-11-20 product update adds Wi-Fi setup and rebrands the OS. The
requirements below include that update. Firmware/agent paths and protocol
names containing `playground` stay compatible with existing programs.

You are implementing the repository that contains this file.

Build a flashable Linux image for the Milk-V Duo S (SG2000) and the
PlatformIO projects that load C906 programs onto a board running that image.
A person with a blank microSD card and a second computer must be able to
flash the card, boot the board, and upload a real-time program without
compiling Buildroot and without replacing `fip.bin`.

Follow this document. Where it locks a choice, keep that choice. Record
anything you learn about the SDK in `docs/sdk-notes.md` with the command
and the result. Do not invent a load address, a GPIO number, or a remoteproc
driver that you have not seen in the pinned SDK.

## Outcome

Shipping artifacts:

- an external release directory containing `beitlab-robotics-milkv-duo-os-<version>-duos-arm64-sd.img`, a complete SD card image.
- `SHA256SUMS` next to the external image covering the image and the agent binary.
- `docs/FLASH.md`, the flash and first-boot steps.
- Three PlatformIO examples under `examples/platformio/` that upload to a
  board booted from that image.
- A starter FreeRTOS program already running on the C906 after first boot.

The person flashing the card does not install the agent afterwards. The
person on the PlatformIO computer does not clone the Milk-V SDK and does
not rebuild the SD image to change the C906 program.

## Board and roles

The Duo S has one Cortex-A53 and one C906 that can run together, plus a
switch that selects ARM or RISC-V for the large core. This image uses the
ARM large core.

| Core | This image |
| --- | --- |
| Cortex-A53 at 1 GHz | Linux. Network, SSH, the agent. |
| C906 at 700 MHz | FreeRTOS. The program PlatformIO uploads. |
| 8051 | Leave it unused. |

The large-core C906 at 1 GHz and the A53 do not run at the same time. The
image is ARM-only. `docs/FLASH.md` tells the user to set the boot switch to
ARM before power-up. A boot log that starts with `C` means the switch is on
RISC-V; the doc says to power off and switch to ARM.

Linux and the C906 program meet through the vendor mailbox
(`/dev/cvi-rtos-cmdqu`) and through `remoteproc`. They do not meet through
J22. J22 is the serial console for a human.

This image must boot on a Duo S with nothing stacked on it. It must also
boot when the Duo S is mounted on the Beitlab carrier
(`../Robotics_board`). On the carrier, do not document USB-C as a power
source or as the network link. The carrier feeds VSYS, and powering the
USB-C port at the same time is an open hardware question. First-boot
network can use the onboard Ethernet jack or the serial Wi-Fi setup below.

## What this repository owns

```
duo-s-playground/
  PROMPT.md              this contract
  README.md
  AGENTS.md
  sdk.lock               URL, commit, target name, toolchain file hash
  image/                 Buildroot/SDK overlay: agent, systemd, hostname, banner
  agent/                 rtos-agent sources and its small test
  firmware/starter/      FreeRTOS program baked into the first boot
  examples/platformio/   hello, mailbox_ping, heartbeat
  scripts/               fetch-freertos, build-image, flash helper
  docs/FLASH.md
  docs/sdk-notes.md
  .github/workflows/     image build and GitHub Release assets
```

Do not copy `Balanced_robot` or `Robotics_board` into this tree. The third
example may mention those projects in a comment. It does not compile them.

## SDK pin

Base SDK: `https://github.com/milkv-duo/duo-buildroot-sdk-v2`.

Target name: `milkv-duos-glibc-arm64-sd`.

Board config already enables FreeRTOS. Confirm these lines in
`build/boards/cv181x/sg2000_milkv_duos_glibc_arm64_sd/sg2000_milkv_duos_glibc_arm64_sd_defconfig`
at the commit you pin, and fail the build if they are absent:

```
CONFIG_ENABLE_FREERTOS=y
CONFIG_ENABLE_RTOS_DUMP_PRINT=y
```

Pick a commit, record it in `sdk.lock`, and build only that commit.
`sdk.lock` fields:

- `sdk_url`
- `sdk_commit` (full SHA)
- `target` = `milkv-duos-glibc-arm64-sd`
- `freertos_script` = `freertos/cvitek/build_cv181x.sh`
- `host_os` = `Ubuntu 22.04 amd64`, with the SDK Docker path as the
  supported alternative

The image build needs the full SDK and Milk-V `host-tools`. The PlatformIO
examples need a sparse checkout of `freertos/cvitek` at the same commit,
fetched into an external cache by `scripts/fetch-freertos.sh`. A person uploading an example must
not need the rest of the SDK.

Build host for the image is Ubuntu 22.04 amd64, or the Docker environment
the SDK documents for other distributions. Record the actual host in
`docs/sdk-notes.md`.

## Image identity

| Item | Value |
| --- | --- |
| Product name | `beitlab Robotics MilkV duo OS` |
| Hostname | `beitlab-milkv-duo` |
| mDNS | `beitlab-milkv-duo.local` via Avahi |
| Linux user | `beitlab` (UID/GID 1000) |
| Root login | Locked password; SSH root login disabled |
| Default user password | None; choose one per device before flashing or on J22 |
| Agent command | `/usr/bin/rtos-agent` |
| Agent service | `rtos-agent.service`, enabled |
| Starter firmware name | `/lib/firmware/playground.elf` |
| Version file on device | `/etc/duo-playground-release` |

`/etc/duo-playground-release` contains the image version, the `sdk.lock`
commit, and the starter firmware git description.

First boot on the serial console (115200 8N1) prints:

- the release line
- `beitlab-milkv-duo.local`
- the regular SSH user `beitlab` and the requirement to provision a public key
- one line saying there is no default password and root SSH is disabled

Create `beitlab` with a private home and a locked initial password. Keep
root's password locked and set `PermitRootLogin no`. SSH uses public keys
by default. The PC helper installs public keys for `beitlab`, never root,
and consumes the boot key file. Accept a hidden, confirmed, per-device
user password (`--user-password`, at least 12 characters) for sudo/local
login, storing only its SHA-512 crypt hash in `playground/user.conf`.
Password SSH requires an explicit `--ssh-password-login` opt-in together
with a user password. Never ship a universal password or a private key.
Provide a bounded J22 first-user password prompt when no chosen password
is saved; unattended boot must reach the normal login prompt.

Use sudo with password authentication for administration. Passwordless
sudo is limited to a root-owned `rtos-control` helper with a strict command
allowlist: status, log [-f], recover, lock, unlock, ping <nonce>, and load
from stdin. It accepts no arbitrary command or file path. Stage uploaded
bytes privately in /run, bound their size, call the existing root-only
agent, and remove the staging file on success/failure. Do not make the
agent setuid or grant broad passwordless sudo. C906 uploads remain capable
of controlling the real-time hardware; this is not a sandbox for hostile
firmware. The Linux userspace still must not reboot on upload.

The PC helper must reject account/key customization of older root-login
images using a new `playground/user-supported` capability marker. Standard
SSH and the Paramiko transport must verify known host keys; perform an
interactive first SSH connection before PlatformIO uploads. Explicit
`PIO_DUO_USER=root` may retain compatibility with older images.

Install Avahi and OpenSSH. Set the MOTD to the release line and the URL of
this repository's README.

The OS product name also appears in `/etc/issue` and `/usr/lib/os-release`.
Start the AIC8800 Wi-Fi drivers with a systemd unit from the first boot;
do not depend on the vendor SysV init scripts. Keep DHCP for Ethernet and
Wi-Fi under one dhcpcd instance, preferring Ethernet. Reconnect a saved
Wi-Fi profile automatically.

Allow SSID/password provisioning before flashing, similar to an imager's
network setup. Provide a PC helper that creates a personal copy of the
image with a boot-volume `playground/wifi.conf`, without rebuilding Linux
or mounting the image as root. Also support writing that file to a mounted
boot volume. Prompt for a hidden password, encode the SSID safely and
derive the WPA PSK without saving the plaintext password. Import the
profile with root-only permissions before wpa_supplicant starts, consume
the boot file, and connect automatically on the first boot. Keep release
images free of credentials. Document that a personal image contains a
network secret and that hardware connectivity needs a fresh-card test.

The PC helper also accepts an optional hostname and one or more SSH public
key files, in either image-copy or mounted-boot-volume mode. Provide
`scripts/configure-image.py` for this combined setup, and keep the Wi-Fi
helper compatible. Allow hostname/key-only setup without a Wi-Fi prompt.
Never accept a private key as an authorized key. Carry these settings on
the FAT volume as `playground/hostname` and `playground/authorized_keys`.
Validate the hostname as a single DNS label, apply it persistently and at
runtime before DHCP, Avahi and SSH start, update the serial banner, and
consume the hostname file. Import regular-user keys before SSH; password login is disabled
unless explicitly enabled as described above. Distinguish images supporting these settings from older images
that support only Wi-Fi; reject unsupported customization on the PC.
Keep the original release image and all non-boot partitions unchanged.

Provide `scripts/beitlab-imager.py`, a PC-side guided CLI with beitlab
branding/emojis, hidden Wi-Fi password entry, supported hostname/account
setup, removable-drive discovery and an integrated flash/verify step.
Use a supplied local image first. Support future public GitHub release
assets when a repository is configured, checking SHA256 before use. Do
not invent a download repository. Protect system/internal/source disks,
require exact device-name confirmation, recheck the selected device before
writing, and verify the written image by reading it back. Keep discovery
and preparation modes free of device writes. Document supported host OSes
and distinguish automated tests from physical flashing tests.

Also support the configured private repository's release assets with an
explicit `--github-auth` option using GitHub CLI's existing login. Keep
tokens out of command arguments and Python download headers. Require an
explicit release tag for a draft; keep `latest` limited to published
releases. Verify authenticated downloads before personalizing the image.

Provide `--flash-only --image <prepared-image>` to burn an existing
personalized image without asking for Wi-Fi, hostname or account settings
again. Preserve the input image and its settings, verify its checksum and
boot header, and retain the same drive protections, exact confirmation
and read-back verification used by the guided setup flow. Do not require
mtools or a GitHub login for this local flash-only path.

If neither Ethernet nor Wi-Fi has a usable address after a short startup
grace period, ask on J22 whether to configure Wi-Fi before the login prompt.
An unattended boot must time out to the normal login. Accept an SSID and
hidden password, retry several times, and allow corrected credentials or
cancellation. Save successful credentials privately, and restore the old
profile on failure or cancellation. Provide `beitlab-wifi-setup` to reopen
the wizard after login. `rtos-agent.service` must start automatically
before this prompt, with no dependency on having a network connection.

The boot partition stays the SDK layout: FAT volume labeled `boot`,
containing `fip.bin` and `boot.sd` (sourced from `rawimages/boot.sd` by
the SDK's `genimage.cfg`). U-Boot loads `boot.sd` from the FAT root.
Do not ask the user to copy `cvirtos.bin` onto that partition.

## Real-time load path

This is the requirement that makes the playground worth shipping.

After Linux is up, replacing the C906 program is:

1. Copy a new ELF to the board.
2. Stop the C906.
3. Start it on that ELF.
4. Leave the A53 Linux userspace running. SSH sessions may stay up.

The mechanism is the kernel `remoteproc` class, node
`/sys/class/remoteproc/remoteproc0`, firmware file
`/lib/firmware/playground.elf`.

At the pinned commit, search the SDK for the C906 remoteproc driver
(names seen in Milk-V history include the Arduino-branch patches and any
`remoteproc` node for the second C906). Record the driver file, the device
tree node, and the firmware ABI in `docs/sdk-notes.md`.

If the pinned commit does not expose `remoteproc0`:

- Bring in the smallest upstream-relative patch that does, and store it
  under `image/patches/` with a header that names the source commit or
  mailing-list message.
- The patch must apply to the pinned SDK. Rebase notes go in
  `docs/sdk-notes.md`.

If no such driver can be made to stop a core that the bootloader already
started, stop and write the blocker in `docs/sdk-notes.md`. Do not ship an
image whose only update path is "replace `fip.bin` and reboot". That path
may remain as a recovery step in `docs/FLASH.md`. It is not the upload path.

The starter ELF and every PlatformIO ELF use the ABI that driver requires.
If the driver requires a `.resource_table` section, all four programs have
one, generated from the same header in `firmware/include/resource_table.h`.
Do not hard-code the DRAM run address from blog posts. Read it from the
pinned board memmap and from the driver, and quote both in
`docs/sdk-notes.md`.

Boot behavior:

- Power-on starts the starter ELF, either from `fip.bin` (BLCP_2ND,
  `cvirtos.bin`) or by the kernel autostarting `playground.elf`. Pick the
  one the driver supports, and document it.
- `rtos-agent load` replaces that program without rebooting Linux.
- A failed ELF does not brick Linux. `rtos-agent recover` restarts the
  starter ELF that remains stored at `/lib/firmware/playground-starter.elf`.

Prove this on hardware or on a note that hardware was unavailable. The
acceptance section below says which proof is required before calling the
image done.

## Starter firmware

`firmware/starter/` is a FreeRTOS program built by the SDK C906 toolchain
(`freertos/cvitek/scripts/toolchain-riscv64-elf.cmake` and
`build_cv181x.sh`'s `CVIRTOS` type). Link it as `playground.elf` and also
install it as `playground-starter.elf`.

Behavior:

- On start, print `playground starter <version>` on the small-core console.
  Those lines show up on UART0 with the vendor `RT:` prefix. UART0 is also
  the Linux console. Printing from the C906 is allowed. Taking the UART
  away from Linux is not.
- Answer a mailbox ping. Linux sends command id `0x40` with `param_ptr`
  equal to a nonce. The starter replies on the mailbox with the same nonce
  plus one. Use a command id that does not collide with the vendor ids in
  `freertos/cvitek/driver/rtos_cmdqu/include/rtos_cmdqu.h` at the pinned
  commit. Record the chosen id in `docs/sdk-notes.md` and in
  `firmware/include/playground_cmd.h`.
- Touch no GPIO that the carrier uses for motors, PWM, SPI, or I2C.
  The starter may toggle the Duo S LED only if the schematic for this board
  revision shows a GPIO LED that is not shared with the carrier sockets.
  If you cannot show that, the starter toggles nothing.

The starter is the program a fresh card runs. It is also the program
`rtos-agent recover` returns to.

## Agent

Implement `rtos-agent` in C, static, no Python on the device. Python on the
PlatformIO computer is fine. The Duo S image has 512 MB of RAM; do not add
a language runtime for this one service.

The binary speaks a line protocol on stdin/stdout so SSH can drive it, and
the same functions are what the systemd service uses for status.

Commands:

```
rtos-agent status
rtos-agent load <path-to-elf>
rtos-agent recover
rtos-agent log
rtos-agent lock
rtos-agent unlock
```

`status` prints one block a script can parse:

```
state=running|stopped|faulted
firmware=/lib/firmware/playground.elf
lock=locked|unlocked
release=<image version>
```

`load`:

1. Refuse unless the caller is root.
2. Refuse while the lock is locked. Exit code 3, message `locked`.
3. Parse the file. Refuse it unless it is an ELF for RISC-V, 64-bit, and
   contains the sections the remoteproc note says are required. Exit code
   2 on a bad file. Do not write it over the live firmware.
4. Copy it to `/lib/firmware/playground.elf.new`, fsync, then rename it
   onto `/lib/firmware/playground.elf`.
5. Write `playground.elf` into `remoteproc0/firmware`.
6. Write `stop` to `remoteproc0/state`, then `start`.
7. Wait up to 5 seconds for `state` to read `running`. On failure, call the
   recover path and exit 4.

`recover` starts `/lib/firmware/playground-starter.elf` the same way and
never deletes that file. `load` must not overwrite `playground-starter.elf`.

`log` prints the C906 text the kernel captured for this remoteproc
(the `remoteproc` trace or the vendor dump the driver exposes). If the
pinned driver has no trace buffer, `log` prints that fact and the path of
the UART device the `RT:` lines share with Linux, and `docs/FLASH.md` says
the serial console is the log for that build. Do not take `/dev/ttyS0`
away from the login console inside the agent.

Lock:

- Factory state is `unlocked`, stored in `/etc/rtos-agent/lock` as the word
  `unlocked`. A Duo S with no carrier can take uploads immediately.
- `lock` writes `locked`. `unlock` writes `unlocked`.
- `docs/FLASH.md` tells a carrier user to run `rtos-agent lock` before the
  wheels can move, and to open the E-stop before `unlock`. The agent cannot
  see the carrier E-stop: those pins belong to the C906 in the robot pin
  map, and this image must not claim them in the Linux device tree.
- The device tree overlay for this image disables Linux claims on the pads
  listed in `../Balanced_robot/include/balance/board_carrier.h` (SPI3, I2C4,
  PWM controllers 0–2, and the named GPIOs). Linux keeps Ethernet, USB,
  the SD card, UART0, and the mailbox. Quote the overlay in
  `docs/sdk-notes.md`.

systemd:

- `rtos-agent.service` is oneshot-or-idle: it does not sit in a hot loop.
  SSH invokes the binary. The unit's job is to set the lock file default
  and to confirm `remoteproc0` exists at boot, logging a single error if
  it does not.
- Journal entries for each `load`: filename, sha256, result.

Network exposure of the agent is SSH only. Do not open a second TCP port.

## PlatformIO examples

All three live under `examples/platformio/` and share
`examples/platformio/common/` for the upload script, the resource table
header, and the mailbox command id.

The PC toolchain is the RISC-V ELF toolchain the pinned FreeRTOS tree
expects. `scripts/fetch-freertos.sh` downloads that toolchain if the SDK
documents a standalone tarball; otherwise it tells the user to use the
`host-tools` tree and records the path. PlatformIO's `platformio.ini`
calls the fetched compiler. It does not call `./build.sh` and it does not
rebuild `fip.bin`.

Common `platformio.ini` settings, factored so each example repeats only
its name:

- `platform = native` is the wrong platform. Use a custom platform script
  or `platform = chipsalliance` only if you have verified the flags match
  `toolchain-riscv64-elf.cmake`. Mismatched `-march` is a failed example.
- `upload_protocol = custom`
- `upload_command` = `python3 ../common/upload.py $SOURCE`
- `monitor` = `python3 ../common/monitor.py`
- Default upload host `beitlab-milkv-duo.local`, user `beitlab`, overridable with
  `PIO_DUO_HOST` and `PIO_DUO_PASS`.

`upload.py`:

1. `ssh` to the host.
2. Copy the ELF to a unique private temporary file in the regular user home.
3. Stream it on stdin to `sudo -n /usr/libexec/playground/rtos-control load`,
   then delete the user-owned upload file. The helper stages it on tmpfs.
4. Print the agent stdout. Exit with the agent's code.
5. On exit code 3, print: the board is locked; open the E-stop and run
   `ssh beitlab@beitlab-milkv-duo.local sudo -n /usr/libexec/playground/rtos-control unlock`.

`monitor.py` runs `ssh beitlab@<host> sudo -n /usr/libexec/playground/rtos-control log` and keeps the stream
open. Document the Ctrl-C behavior.

Examples, each a program a new user can upload on a bare Duo S:

### `hello`

Print `playground hello <build-id>` once per second from a FreeRTOS task.
`<build-id>` is the compile date. No GPIO. This is the first example
`docs/FLASH.md` runs.

### `mailbox_ping`

Same mailbox contract as the starter (`playground_cmd.h`). A task waits
for command `0x40` and replies with nonce plus one. Include
`examples/platformio/mailbox_ping/host_ping.sh`, which runs on the Duo S
(or from the PC over SSH) and uses `/dev/cvi-rtos-cmdqu` to send a nonce
and check the reply. The ioctl layout must match the `cmdqu_t` struct in
the pinned `rtos_cmdqu.h`. Copy the struct into the script's accompanying
`host_ping.c` rather than redeclaring it from memory.

### `heartbeat`

Toggle one GPIO at 1 Hz. The pin is a `#define` in `heartbeat/pin.h` with
the default `PLAYGROUND_GPIO_ENABLE` set to 0, so a build without an
edited pin toggles nothing and prints `heartbeat idle` instead. The
comment above the define lists the rule: the pin must be a Duo S header
pin that `board_carrier.h` does not assign, and the user writes the pad
name only after reading the Duo S schematic for their board revision.
Do not ship a default pad.

Each example README is ten lines: what it does, the upload command, what
the user should see.

### Carrier IMU diagnostic

Provide `examples/platformio/robotics_imu` for carrier revision 0.11's
BMI323 at I²C4 address 0x68 on B1/B2. Poll using I²C only, with no IMU
interrupt GPIO, PWM, SPI, LED or motor control. Print acceleration, gyro
and temperature through the existing serial/dump-ring runtime. Add an
explicit diagnostic profile granting only that device while retaining
carrier and Linux reservations. Check policy before any register access,
isolate the SDK's duplicate boot I²C route as inputs, and bound all
transactions and cleanup. Build independently of both sibling trees and
document hardware tests as pending until real readings are captured.

Also provide `examples/platformio/beitlabduino_imu` using setup()/loop(),
bl_print()/bl_delay() and an optional beitlabduino IMU API. Share the
polling transport/decoder between both examples, retain the same profile
and pin restrictions, and return explicit ready/wait/error states without
changing the caller's output on an unsuccessful read.

### Standalone BMI160 on J3

Provide `examples/platformio/beitlabduino_bmi160_j3` for a Duo S with no
robotics carrier. Use I²C4 on J3 pin 3/B20 (SCL) and pin 5/B21 (SDA),
with 3.3 V module power/logic and beitlabduino setup()/loop(). Select a
separate `duos_bare_bmi160_j3` profile inheriting the bare board; retain
Linux reservations and forbid the J3 route on carrier profiles. Permit
only 0x68/0x69 identity probes and configure only BMI160 chip ID 0xD1.
Use its byte protocol, suspend-safe individual writes, minimum timing,
power/range checks, bounded transfers and unchanged output on failure.
Share the existing I²C transport while preserving carrier routing.

## Flash document

`docs/FLASH.md` is the only page a new user needs. It contains:

1. Host tools: balenaEtcher, Raspberry Pi Imager, or `dd`. The `dd` example
   uses `lsblk` and tells the user to read the device name twice.
2. Boot switch on ARM. Power from USB-C only when the Duo S is not on the
   carrier. On the carrier, power the carrier and use Ethernet.
3. Serial console 115200 8N1 if they want the boot log. Ethernet is enough
   for upload.
4. `ping beitlab-milkv-duo.local`, then
   `ssh beitlab@beitlab-milkv-duo.local` with the provisioned SSH key.
5. From `examples/platformio/hello`: the PlatformIO upload command and the
   line they should see in the monitor.
6. Recovery: if a bad ELF wedges the C906,
   `ssh beitlab@beitlab-milkv-duo.local sudo -n /usr/libexec/playground/rtos-control recover`. If Linux itself
   will not boot, rewrite the SD card. Replacing `fip.bin` by hand is
   mentioned as a developer recovery, with a warning that a bad `fip.bin`
   also drops Linux.
7. Carrier warning, in one short paragraph: lock the agent before the
   robot can move; the upload path restarts the core that will own the
   motor pins once robot firmware is loaded.

## Build scripts

`scripts/build-image.sh`:

- Reads `sdk.lock`.
- Clones or fetches the SDK into an external cache (PLAYGROUND_SDK) at that commit.
- Applies `image/patches/`.
- Installs the overlay: hostname, release file, agent, systemd unit,
  device tree overlay, starter ELF, Avahi service.
- Runs `./build.sh milkv-duos-glibc-arm64-sd`.
- Copies the resulting image to an external release directory with its version in the filename and
  writes `SHA256SUMS`.

Fail the script if `remoteproc0` is not present in the built kernel config
or device tree. Grep the built `.config` and the compiled dtb. A green
Buildroot build that forgot the driver is a failed script.

`scripts/fetch-freertos.sh` is what example users run. It fills
`sdk/freertos/cvitek` and the toolchain only.

## Acceptance tests

Do these in order. Record outputs in `docs/sdk-notes.md`.

1. **Image identity.** Fresh card, ARM switch, Ethernet. `ping` and SSH
   as `beitlab` with the provisioned key work; root SSH and default password login fail. `/etc/duo-playground-release` matches
   `sdk.lock`. `systemctl` shows `rtos-agent.service` active.
2. **Core came up with Linux.** Within 30 seconds of boot,
   `rtos-agent status` prints `state=running`. Serial shows
   `RT:` and `playground starter`.
3. **Hello upload.** From a second computer, PlatformIO upload of `hello`
   returns 0. `status` stays `running`. Monitor or serial shows
   `playground hello`. The SSH session used for the test is still alive
   after the upload. `uptime` on Linux did not reset.
4. **Bad file.** `rtos-agent load` of a text file exits 2 and the previous
   program is still running.
5. **Lock.** `rtos-agent lock`, then upload, exits 3. `unlock`, then
   upload, exits 0.
6. **Recover.** Load `hello`, then `rtos-agent recover`. The starter banner
   returns. `playground-starter.elf` still exists.
7. **Mailbox.** Upload `mailbox_ping`. `host_ping` sends nonce 7 and
   observes 8.
8. **Carrier device tree.** On the built dtb, the pads named in
   `board_carrier.h` are not claimed by a Linux driver. Ethernet and
   `/dev/cvi-rtos-cmdqu` still exist. This test is a dtb inspection and
   does not need the carrier.

A test that could not be run because no board was on the bench stays
listed as pending in `docs/sdk-notes.md`. Do not mark it passed.

## Out of scope

- The balance controller, FOC, and `sg2000_hal.h`. Those stay in
  `Balanced_robot`.
- Rebuilding `fip.bin` as the normal upload.
- A web IDE. SSH and PlatformIO are the client. The agent is a C program
  with a line interface so a web page can call it later.
- Programming the 8051.
- Requiring Wi-Fi hardware: Ethernet must also work on boards without it.
- Changing the carrier schematic or the robot pin map.
- Claiming that USB-C is safe while the carrier powers the Duo S.

## Done

The work is done when `scripts/build-image.sh` produces the image, the
eight acceptance tests have results recorded, and a new user can follow
`docs/FLASH.md` from a blank card to the `hello` banner without reading
this prompt.

## Source-only repository and CI

Keep SDK caches, binaries, images, build logs and output folders outside
the source checkout. The repository must have no `out/` or vendored `sdk/`
directory. Defaults live under `~/.cache/beitlab-duo-os`; allow
BEITLAB_WORK_DIR, PLAYGROUND_SDK, BEITLAB_BUILD_DIR and BEITLAB_OUTPUT_DIR
overrides. Fetch only the sparse FreeRTOS/toolchain dependencies for PC
examples. Full SDK fetching is restricted to the image build.

Provide GitHub Actions source tests and an on-demand/version-tag image
build using the pinned SDK. Save build diagnostics as Actions artifacts
and attach compressed images, imager tools, selected notices and verified
checksums to a draft GitHub Release. Keep build tokens read-only and grant
release write permission only to the publishing job. Account for SDK disk
requirements, using an explicit free-space check and configurable runner.
CI must check carrier pad claims from the in-repo profile snapshot without
importing or requiring the sibling robot/carrier repositories.
