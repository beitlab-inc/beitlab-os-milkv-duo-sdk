# beitlab Robotics MilkV duo OS: flash and first boot

This page takes you from a blank microSD card to the `hello` program running on the Duo S small core. You need:

- a Milk-V Duo S,
- a microSD card (1 GB or more),
- an Ethernet cable to your network, or a Wi-Fi-equipped Duo S (a serial adapter is optional when Wi-Fi is preconfigured),
- a second computer.

Image file: `beitlab-robotics-milkv-duo-os-<version>-duos-arm64-sd.img`. Download it from the repository Releases or Samba. Local builds write to `~/.cache/beitlab-duo-os/releases/<version>/`, alongside `SHA256SUMS`; no `out/` directory is kept in the source checkout. See [CI.md](CI.md).

## 1. Write the card

### Guided beitlab Imager CLI

For an interactive terminal workflow with beitlab branding, emojis and progress bars, install Python 3.9+ and `mtools`, then run:

```sh
sudo apt install mtools                 # Linux; on macOS: brew install mtools
python3 scripts/beitlab-imager.py --image /path/to/your.img
```

Run the wizard as your normal user. It requests administrator access only for writing the disk. This version supports native Linux and macOS; native Windows raw-disk flashing is not implemented. macOS discovery has fixture tests; physical card writing still needs hardware validation.

The wizard guides you through four steps:

1. **📦 Image:** verify the local image's SHA256. A matching `.img.sha256` or adjacent `SHA256SUMS` is used automatically; otherwise enter the release checksum or pass `--sha256 <digest>`. `.img`, `.img.xz` and `.img.gz` are supported, with verification before unpacking.
2. **⚙️ Board settings:** enter SSID/country and confirm a hidden Wi-Fi password. Hostname setup is offered where supported. OS 0.4.0 also offers a `.pub` SSH key or a unique SSH password for `beitlab`. Older images keep their existing login settings; the CLI does not change the image's account model. Passwords never appear in shell arguments or logs.
3. **💾 Card:** choose a removable drive by number, model, path and capacity. Insert a card and press `r` to refresh. Internal/system disks, read-only media, active storage mappings and disks holding imager source files are blocked. `q` cancels this step; Ctrl-C cancels anywhere.
4. **🚀 Flash:** review settings and target, then type the complete phrase shown, such as `ERASE /dev/sdc`, including the drive path. A partial entry such as `ERASE` asks again without writing or discarding the prepared settings. Enter `q` or press Ctrl-C to cancel. The writer rechecks device identity, unmounts volumes, writes and flushes the image, and reads it back to verify the checksum. Cancelling before writing leaves the drive unchanged; an interrupted write requires reflashing before use.

The original image is kept. Personalized temporary images are removed when the wizard exits. Add `--output /path/to/my-duo.img` to keep a personal copy and checksum; the copy contains a network PSK and should stay private. The completed wizard prints the correct SSH account/hostname and ARM switch/power instructions.

```sh
python3 scripts/beitlab-imager.py --list-drives
python3 scripts/beitlab-imager.py --image /path/to/your.img --dry-run
python3 scripts/beitlab-imager.py --image /path/to/your.img \
  --prepare-only --output /path/to/my-duo.img
```

`--list-drives` needs no image, administrator access or mtools. `--dry-run` performs setup without selecting, unmounting or writing a disk. `--prepare-only` saves an image for another writer. Add `--no-emoji`/`--no-color` for plain output; `NO_COLOR` is honored. Non-interactive password entry is refused if it would expose a password.

**GitHub downloads.** A supplied `--image` always wins. Without it, configure `github_repository` in [imager.json](../imager.json), set `BEITLAB_IMAGE_REPO=OWNER/REPO`, or pass:

```sh
python3 scripts/beitlab-imager.py --github-repo OWNER/REPO
python3 scripts/beitlab-imager.py --github-repo OWNER/REPO --release v0.4.0
```

The GitHub `origin` remote is also detected when available. With no repository configured, the CLI prompts for a local image and offers a local external-cache image if present. The configured repository is `beitlab-inc/beitlab-os-milkv-duo-sdk`. It is private: download while authenticated using a browser or `gh release download`, then pass `--image`. Automatic unauthenticated downloads require a public published release.

For deployment, attach `beitlab-robotics-milkv-duo-os-<version>-duos-arm64-sd.img` (optionally `.xz`/`.gz`) to a public release. The CLI uses the GitHub asset's SHA256 digest or a matching `SHA256SUMS`/`<asset-name>.sha256` asset. Compressed assets need the compressed file's checksum. Missing hashes, mismatches and HTTPS downgrades are refused. Verified downloads are cached in `~/.cache/beitlab-imager`; incomplete downloads are discarded. The latest published release is the default. The CLI downloads assets; it does not publish releases.

### Configure an image directly

Release images may be compressed as `.img.xz`. For the direct helper below, extract with `xz -dk /path/to/image.img.xz` first and verify the extracted `.img` using its adjacent `.img.sha256` sidecar. The guided imager handles decompression automatically.

**Configure the board before flashing (required for unattended SSH, OS 0.4.0 or later).** You can prepare Wi-Fi, a hostname and your SSH public key together, so the first boot is ready for SSH. On the PC, install Python 3, OpenSSL and `mtools` (`sudo apt install mtools openssl` on Ubuntu/WSL, or `brew install mtools` on macOS). Verify the release checksum first:

```sh
(cd "$HOME/Downloads" && sha256sum -c SHA256SUMS --ignore-missing)
python3 scripts/configure-image.py \
  --image $HOME/Downloads/beitlab-robotics-milkv-duo-os-0.4.0-duos-arm64-sd.img \
  --output $HOME/Downloads/my-duo.img \
  --ssid "Your network" --country CO \
  --hostname robot-01 --ssh-key ~/.ssh/id_ed25519.pub --user-password
```

Use your country's two-letter code. Enter the password at the hidden prompt; omit `--ssid` to enter the network name interactively too. Hostname is optional. Provision an SSH public key for unattended access. `--user-password` prompts twice for a unique password (at least 12 characters) used for sudo and local login. It does not enable password SSH; add `--ssh-password-login` explicitly to enable it. Root SSH is always disabled, and no default password is provided. Use a hostname without `.local`, containing letters, digits and hyphens; it is normalized to lowercase. Give the **public** `.pub` key, and repeat `--ssh-key` to add more keys. The private key stays on your PC. If you need a key pair, create one with `ssh-keygen -t ed25519`.

The helper creates `$HOME/Downloads/my-duo.img` and `$HOME/Downloads/my-duo.img.sha256`, keeping the original image and release checksums unchanged. It refuses to overwrite existing output files and rejects old images without the requested provisioning support. No Linux rebuild, root access or image mounting is needed. `scripts/configure-wifi.py` remains available with the same options.

For Ethernet with hostname/SSH keys only, add `--no-wifi` and omit `--ssid`/`--country`; no Wi-Fi password is requested.

Flash **`$HOME/Downloads/my-duo.img`** with any tool below. With the Linux flash helper, run `sudo scripts/flash.sh /dev/sdX $HOME/Downloads/my-duo.img`; it verifies the personal checksum. Keep the personal image private: its derived WPA PSK grants access to your network. The profile supports WPA/WPA2 Personal and hidden SSIDs, with 1–32 UTF-8 bytes for the SSID and 8–63 bytes for the password. The unmodified release image contains no network credentials.

Use one of these tools.

- **balenaEtcher**: Flash from file → choose the `.img` → choose the card → Flash.
- **Raspberry Pi Imager**: Choose OS → Use custom → choose the `.img`. Choose Storage → the card. Do not apply OS customisation.
- **`dd`** on Linux:

  ```sh
  (cd "$HOME/Downloads" && sha256sum -c SHA256SUMS --ignore-missing)
  lsblk -o NAME,SIZE,MODEL,TRAN      # card out
  # insert the card
  lsblk -o NAME,SIZE,MODEL,TRAN      # card in: the new line is your card, e.g. sdb
  ```

  Read the device name twice. Compare the size and the model with your card. `dd` erases whatever disk you name, including your computer's own disk.

  ```sh
  sudo dd if=$HOME/Downloads/beitlab-robotics-milkv-duo-os-<version>-duos-arm64-sd.img of=/dev/sdX bs=4M conv=fsync status=progress
  sync
  ```

  `sudo scripts/flash.sh /dev/sdX /path/to/image.img` does the same. It first checks the checksum and refuses system disks. It also makes you type the device name a second time.

**Alternatively: configure the card after flashing.** Mount the card's `boot` volume on your PC, then run:

```sh
python3 scripts/configure-image.py --boot-dir /path/to/boot \
  --ssid "Your network" --country CO \
  --hostname robot-01 --ssh-key ~/.ssh/id_ed25519.pub --user-password
```

This option needs Python 3 and OpenSSL when setting a user password. It writes the selected settings as `playground/wifi.conf`, `playground/hostname`, `playground/authorized_keys` and `playground/user.conf` on the FAT volume. It refuses to replace existing setting files. Safely eject the card.

On first boot, the OS installs the Wi-Fi profile with root-only permissions, applies the hostname before network services start, and installs the public keys for `beitlab` (UID 1000) before SSH starts. The selected user password is carried as a salted SHA-512 crypt hash, never plaintext. Wi-Fi, hostname, user settings and key provisioning files are consumed after successful import. Boot files are treated as data. The boot mount and saved Wi-Fi profile are private to root. The agent starts independently of network setup.

After flashing the example above and booting, connect with:

```sh
ssh beitlab@robot-01.local
export PIO_DUO_HOST=robot-01.local  # use this address for PlatformIO as well
```

Verify the host-key fingerprint on the serial console (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`) before accepting the first connection. PlatformIO requires that host key in `~/.ssh/known_hosts` and rejects unknown or changed keys. Your PC's SSH client uses the matching private key. The remaining examples use the default `beitlab-milkv-duo.local`; substitute your hostname if you customized it. No serial setup or manual `insmod` is required when the configured network is available.

**User access.** The account is `beitlab`, with a private home. Root has a locked password and cannot log in over SSH. Before flashing, configure a public key and preferably a unique user password for administration. With a key only, firmware upload/status/recover still work through the restricted helper, while general sudo needs a user password. On J22, the initial account wizard can set that password before the login prompt; the first question times out after 30 seconds and the entire setup is bounded to three minutes. No configured key or password means the account remains locked until provisioned.

## 2. Boot switch and power

1. With the board unpowered, set the Duo S **boot switch to ARM**.
2. Insert the card.
3. Connect **Ethernet**, use preconfigured Wi-Fi, or attach the J22 serial console for Wi-Fi setup.
4. Power the board:
   - **Duo S alone:** power it from USB-C.
   - **Duo S on the Beitlab carrier:** power the carrier. Do not plug USB-C as well. Use the Ethernet jack for the network.

If the serial log starts with `C` instead of the ARM boot messages, the switch is on RISC-V. Power off, set the switch to ARM, and power on again.

## 3. Serial console and first-start Wi-Fi

The boot log is on **J22**, at **115200 8N1** with 3.3 V levels. Attach your terminal before power-on if you want to configure Wi-Fi. Ethernet needs no interactive setup.

The AIC8800 Wi-Fi drivers and saved-network connection start automatically, including on the first boot with a preconfigured profile. After allowing 15 seconds for an existing connection, the board checks Ethernet and Wi-Fi for a usable address. If neither is connected, the serial console asks:

```text
beitlab Robotics MilkV duo OS
No Ethernet or Wi-Fi connection detected.
Connect to Wi-Fi now? [y/N] (30 seconds):
```

Enter `y`, then the SSID and password. Password entry is hidden. The wizard supports WPA/WPA2 Personal networks, including hidden SSIDs; passwords must contain 8–63 bytes. It tries three times, allowing 20 seconds per attempt for association and an address. If it fails, you can enter another SSID/password and retry. Enter `q` at the SSID/retry prompt or during a connection attempt, or press **Ctrl-C** at any point, to cancel. Declining or cancelling returns to the normal login prompt. No answer within 30 seconds skips setup; later input prompts time out after three minutes.

Successful credentials are saved in a root-only profile and reconnect on later boots. Failed/cancelled setup restores the previous profile. The wizard runs again on a later boot if both links are disconnected. To configure Wi-Fi after login, run `sudo beitlab-wifi-setup`. For diagnostics, use `journalctl -b -u beitlab-wifi-import -u beitlab-wifi -u beitlab-wpa`. A board without the wireless module can still use Ethernet.

`rtos-agent.service` starts automatically before this wizard. The C906 starter and agent work even while you are deciding whether to connect Wi-Fi. The agent service normally shows **active (exited)**: SSH invokes the installed binary when needed.

The login prompt shows:

```
beitlab Robotics MilkV duo OS <version> (sdk <commit>, starter <id>)
Network name: beitlab-milkv-duo.local
SSH login: beitlab (provision an SSH public key before boot)
No default password. Configure a user password before flashing or on J22.
Administration: sudo; root SSH login is disabled.
```

Lines from the small core start with `RT:`, for example `RT: playground starter <version>`. They are mixed into the Linux console output. That is expected.

## 4. Connect

Wait about 30 seconds after power-on, then:

```sh
ping beitlab-milkv-duo.local
ssh beitlab@beitlab-milkv-duo.local        # use your provisioned SSH key
sudo -n /usr/libexec/playground/rtos-control status
systemctl is-active rtos-agent.service
```

`sudo -n /usr/libexec/playground/rtos-control status` should print:

```
state=running
firmware=/lib/firmware/playground.elf
lock=unlocked
release=<version>
```

To verify Wi-Fi on a fresh card, boot with Ethernet disconnected and the preconfigured profile, then check over SSH or J22:

```sh
systemctl is-active beitlab-wifi.service beitlab-wpa.service dhcpcd.service
sudo wpa_cli -i wlan0 status
ip -4 addr show dev wlan0
journalctl -b -u beitlab-wifi-import -u beitlab-wifi -u beitlab-wpa
```

The services should be active, `wpa_state=COMPLETED`, and `wlan0` should have a router-assigned address. The journal should show profile import and driver startup without manual loading. Reboot once and confirm it reconnects with the boot provisioning file gone. These hardware checks are required to confirm association, DHCP and reconnect; the build checks verify that the driver, firmware and services are present and enabled.

Administration uses `sudo` and your chosen user password. SSH stays key-only when that password changes. General passwordless sudo is not enabled. If you explicitly enabled password SSH, `PIO_DUO_PASS` can supply that password to PlatformIO.

`.local` names need mDNS. That is built into macOS and most Linux desktops, and into Windows 10 and later. If `ping` fails, use the board's IP address from your router, and set `PIO_DUO_HOST=<ip>` for the steps below. If you re-flash the card, run `ssh-keygen -R beitlab-milkv-duo.local` once, because the board's host key changes.

## 5. Upload `hello` with PlatformIO

On the second computer, do this once. You need Linux x86_64, or WSL2 on Windows.

```sh
git clone https://github.com/beitlab-inc/beitlab-os-milkv-duo-sdk.git duo-s-playground && cd duo-s-playground
pip install platformio               # or the PlatformIO IDE
scripts/fetch-freertos.sh            # FreeRTOS tree and C906 toolchain at the pinned commit
```

Then:

```sh
cd examples/platformio/hello
pio run -t upload                    # uses your key and the restricted sudo helper
pio run -t rtmonitor                 # C906 log over SSH; Ctrl-C to stop
```

The upload ends with `loaded /lib/firmware/playground.elf sha256=... state=running` and exits with 0. The monitor shows this once a second:

```
RTD: playground hello Nov 19 2026 16:42:07
```

The serial console shows the same line with `RT:` in front. Linux keeps running: SSH sessions stay open and `uptime` does not reset. Only the small core restarts.

`pio run -t rtmonitor` runs `ssh beitlab@beitlab-milkv-duo.local sudo -n /usr/libexec/playground/rtos-control log -f`. Ctrl-C closes that SSH session and stops the viewer. The program on the C906 keeps running.

Exit codes from the upload:

| Code | Meaning |
| --- | --- |
| `0` | Loaded and running. |
| `2` | Not a valid C906 ELF. Nothing changed. |
| `3` | The board is locked. See section 7. |
| `4` | The program did not start. The starter firmware is running instead. |

## 6. Recovery

- **A bad program wedged the C906.** Run `ssh beitlab@beitlab-milkv-duo.local sudo -n /usr/libexec/playground/rtos-control recover`. This restarts the starter firmware from `/lib/firmware/playground-starter.elf`. Linux stays up.
- **Linux does not boot** (no ping, nothing useful on serial). Write the card again (section 1).
- **Developer recovery.** The boot partition holds `fip.bin`, which contains the bootloaders and the C906 program FSBL starts at power-on. Replacing it by hand is possible, but it is not the upload path. A bad `fip.bin` stops Linux from booting too, and only re-writing the card fixes that.

## 7. On the carrier: lock before the robot can move

Run `ssh beitlab@beitlab-milkv-duo.local sudo -n /usr/libexec/playground/rtos-control lock` before the wheels can move. An upload or `recover` restarts the C906, and the C906 owns the motor, PWM, SPI and I2C pins once robot firmware is loaded. While the board is locked, uploads fail: the upload prints `locked` and PlatformIO shows `*** [upload] Error 3`. Unlock only with the E-stop open: `sudo -n /usr/libexec/playground/rtos-control unlock`. The agent cannot see the E-stop, because those pins belong to the C906.
