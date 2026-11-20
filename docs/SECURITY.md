# Regular-user access in OS 0.4.0

The legacy SDK autologin helper now requests a normal login; no active service uses automatic root login.

The operator account is `beitlab`, UID/GID 1000, with a mode-0700 home.
Root's password is locked and `PermitRootLogin no` blocks root SSH.
Release images contain no shared password or private key. SSH accepts
public keys by default; password authentication requires an explicit
provisioning choice. Older 0.1–0.3 images retain their previous defaults;
reflash with 0.4.0 to obtain these settings.

Prepare a personal image with your public key and a unique administration
password:

```sh
python3 scripts/configure-image.py \
  --image $HOME/Downloads/beitlab-robotics-milkv-duo-os-0.4.0-duos-arm64-sd.img \
  --output $HOME/Downloads/my-duo.img --no-wifi \
  --ssh-key ~/.ssh/id_ed25519.pub --user-password
```

Omit `--no-wifi` and supply `--ssid`/`--country` when configuring Wi-Fi.
`--user-password` prompts twice without echo, checks a minimum length of
12 characters and writes a salted SHA-512 crypt hash using OpenSSL.
It sets the sudo/local-login password, not the Wi-Fi password, and does
not enable SSH password authentication. Add `--ssh-password-login` only
when you need password SSH. Its use requires `--user-password`.

User/password/key settings require the new `user-supported` boot marker;
the helper rejects older root-login images instead of accidentally
installing a user's keys for root. After successful import, the boot
settings are deleted. The FAT filesystem's mount mask is 0077; personal
images still contain access secrets and must be kept private.

Without preconfigured credentials, J22 offers initial user-password setup
before normal login. The question times out after 30 seconds, with a
three-minute service limit for the entire setup. The account starts
locked. Valid key-only provisioning assigns an undisclosed random password
hash because OpenSSH without PAM rejects a locked account even when a key
is supplied. General sudo still needs a chosen password. To add that
password, provision it before flashing or use the J22 prompt on a later
boot. That prompt stops appearing after a password has been chosen.

Connect once with `ssh beitlab@<hostname>.local`. Verify the host-key
fingerprint using the serial console before accepting it. Both OpenSSH
and Paramiko upload transports require a previously trusted host key and
reject changed keys. Reflashing generates new host keys; remove an old
entry only after checking that the change is expected.

General administration uses password-authenticated sudo through the
`wheel` group. There is no `NOPASSWD: ALL`. The only passwordless sudo
command is the root-owned `/usr/libexec/playground/rtos-control` helper:

| Command | Allowed input |
| --- | --- |
| `status`, `recover`, `lock`, `unlock` | No extra arguments |
| `log` | No arguments or exactly `-f` |
| `ping` | One decimal 32-bit nonce |
| `load` | ELF bytes on stdin; no path argument; maximum 32 MiB |

The helper writes input into a mode-0600 root-owned temporary file in
`/run`, invokes the existing root-only agent and cleans up the temporary
file, including on failure. The agent still validates the ELF, honors the
lock and performs remoteproc stop/start without rebooting Linux.
PlatformIO uses a unique upload file in the regular user's home, streams
it to the helper and removes the user-owned file afterward. No device-node
permissions or kernel interfaces are opened to all users, and the agent
is not setuid. `PIO_DUO_USER=root` is an explicit compatibility option for
older images, not the current default.

The operator may load code onto the C906 and control its peripherals.
This privilege is for trusted robot operators, not for running hostile
firmware in a security sandbox. Keep the carrier E-stop open before an
unlock or firmware change. The agent does not sense the E-stop.

Build checks inspect the final rootfs for account IDs, locked release
passwords, sudo's ownership/setuid mode, private home/sudoers permissions,
SSH defaults and provisioning-service ordering. Host tests exercise
rejected helper arguments, oversized uploads, cleanup, password hashing
and malformed boot profiles. No Duo S is currently connected; physical
first-boot, sudo and upload behavior must also be checked on hardware.

These changes address account/SSH access defaults. They do not establish
secure boot, encrypted storage, update signing or a complete software
security/redistribution audit.

References: [OpenSSH server configuration](https://man.openbsd.org/sshd_config),
[sudo documentation](https://www.sudo.ws/docs/).
