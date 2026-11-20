# beitlabduino_mailbox

This example explicitly selects `custom_profile = duos_bare` for a Duo S without the carrier. The selected [hardware profile](../../../docs/PROFILES.md) controls GPIO permissions; use the [robotics_board example](../robotics_board/README.md) on the carrier.

Linux controls the Duo S on-board LED (pad A29) through the mailbox
between the two cores. The C906 program is written with the beitlabduino
library (`common/lib/beitlabduino`); the Linux side is `bl_send`, a small
tool that runs on the board.

| Command | Value | Reply |
|---|---|---|
| `0x41` LED mode | 0 off, 1 on, 2 blink | the new mode |
| `0x42` blink period | 50-5000 (ms per half period) | the new period |
| anything else | | refused (`BL_CMD_ERROR`) |

It starts blinking at 500 ms.

Before uploading, check in the Duo S schematic for your board revision
that A29 drives only the LED and is not on header J3/J4.

## Two ways to send a command

![Path of one command, ./bl_send.sh 0x41 1, from the Ubuntu PC through
SSH, bl_send, the kernel driver and the mailbox to the C906 program, and
the reply back](mailbox-path.svg)

The mailbox is hardware inside the SG2000 chip, between its Linux core
and the C906. Only Linux on the Duo S can reach it, through
`/dev/cvi-rtos-cmdqu`. The PC has no access to it. So `bl_send` always
runs **on the Duo S**; the only difference is where you type it.
`<board>` below is the board's network name, for example
`duo-playground.local`.

**From the Ubuntu PC** (steps 1 to 9 in the diagram), in this folder:

```bash
./bl_send.sh 0x41 1
```

`bl_send.sh` sees that the PC has no `/dev/cvi-rtos-cmdqu`, so it runs
`python3 ../common/duo_ssh.py bl_send 0x41 1`. That opens one SSH
session to the board, runs `/usr/bin/bl_send 0x41 1` there, and passes
its output and exit code back to your terminal. It is the same as
typing:

```bash
ssh -t beitlab@<board> sudo bl_send 0x41 1
```

`bl_send.sh` adds only the connection settings `upload` uses
(`PIO_DUO_HOST`, `PIO_DUO_USER`, `PIO_DUO_PASS`). Each command opens a
new SSH connection, which takes a moment. That is fine for trying
commands by hand. A PC program can do the same: run
`ssh -t beitlab@<board> sudo bl_send ...`, then read the printed value and the exit
code.

**On the Duo S itself** (steps 2 to 8):

```bash
ssh -t beitlab@<board>            # log in once, from the PC
bl_send 0x41 1              # now on the board: prints 1
bl_send 0x42 100            # prints 100
```

No network is involved per command, so this is the fast way, and the
place for scripts that send many commands (see the loop under "The Linux
side").

In both cases `bl_send` prints only the reply value, and its exit code
says what happened: `echo $?` right after it shows the code (table under
"The Linux side").

## Files

```
platformio.ini      env beitlabduino_mailbox, custom_libs = beitlabduino
src/main.c          the C906 program: setup(), loop(), command handling
linux/bl_send.c     the Linux tool (runs on the Duo S, aarch64)
linux/install.py    builds bl_send on the PC and installs /usr/bin/bl_send
bl_send.sh          runs sudo bl_send from the PC over SSH (or directly on the board)
mailbox-path.svg    the diagram above (plain SVG text, editable)
```

## How the mailbox works

The full explanation, with a diagram of the message path and the table of
ids, is in [`docs/mailbox.md`](../../../docs/mailbox.md). In short:

- Linux sends an 8-byte message, a command id (7 bits) and a 32-bit value,
  and waits up to 1 s for one reply with the same id.
- Ids 0x00-0x4f are free for programs, except 0x40 (ping).
- A command nobody listens for comes back unchanged (the echo).
  beitlabduino sets **bit 31** (`BL_CMD_ACK`) on every reply, and Linux
  never sends bit 31, so `bl_send` can always tell a real reply from an
  echo. Reply values therefore have 31 bits.
- Linux asks and the C906 answers. The C906 does not send messages on its
  own in this example.

## The C906 side

beitlabduino has three mailbox functions (`beitlabduino.h`):

```c
bl_cmd_listen(0x41);          /* in setup(): deliver command 0x41 to me   */
bl_cmd_receive(&cmd, 500)     /* wait up to 500 ms; 1 when a command came */
bl_cmd_reply(&cmd, value);    /* answer it (bit 31 is added for you)      */
```

- `bl_cmd_receive()` looks for commands on every 5 ms tick, so it can
  replace `bl_delay()` in `loop()`. This example waits one blink step with
  it and handles a command as soon as one arrives (so a command makes the
  current blink step shorter).
- Reply exactly once to every command, and soon: Linux gives up after
  1 s by default.
- At most 4 command ids per program (`PG_MAX_SUBSCRIPTIONS`).
- `playground.h` asks for `pg_cmd_subscribe()` in `playground_app_init()`.
  `bl_cmd_listen()` runs later, from `setup()` in a task, so it stops task
  switches while it registers the id.

## Steps

All commands run from this folder.

**1. Upload the C906 program**

```bash
pio run -t upload
```

Expect `[SUCCESS]`. If the board is not found, set the name it answers
to, for example `export PIO_DUO_HOST=duo-playground.local`.

**2. Watch the log** (second terminal)

```bash
pio run -t rtmonitor
```

Expect `beitlabduino mailbox on A29, commands 0x00000041 0x00000042`
(`%x` always prints 8 hex digits), and the LED
blinking at 500 ms.

**3. Install the Linux tool** (once; it stays until the SD card is re-flashed)

```bash
python3 linux/install.py
```

Expect `built .../bl_send` and `installed /usr/bin/bl_send`. It uses the
aarch64 compiler from the SDK host-tools (`sdk.lock`
`host_tools_aarch64_linux`); set `CC` to use another
`aarch64-linux-gnu-gcc`.

**4. Send commands from the PC**

```bash
./bl_send.sh 0x41 1        # LED on        -> prints 1, log "led mode 1"
./bl_send.sh 0x41 0        # LED off       -> prints 0
./bl_send.sh 0x41 2        # blink again   -> prints 2
./bl_send.sh 0x42 100      # fast blink    -> prints 100, log "blink period 100 ms"
./bl_send.sh 0x42 9999     # out of range  -> "refused", exit 4
```

On the board itself (`ssh -t beitlab@<board>`), the same is `sudo bl_send 0x41 1`.
Only root can open `/dev/cvi-rtos-cmdqu`.

## The Linux side

```
bl_send <cmd_id> [value] [timeout_ms]     value default 0, timeout default 1000
```

Numbers may be decimal or `0x` hex. On success `bl_send` prints only the
reply value, so scripts can use it. The exit code says what happened:

| Exit | Meaning |
|---|---|
| 0 | reply printed |
| 1 | bad arguments, or `/dev/cvi-rtos-cmdqu` could not be used |
| 2 | no reply within the timeout, or a reply without bit 31 |
| 3 | echoed unchanged: the running C906 program does not listen for that id |
| 4 | refused: the program replied `BL_CMD_ERROR` |

A shell script on the board can drive the C906 with it:

```sh
for p in 400 200 100 50; do sudo bl_send 0x42 $p && sleep 2; done
```

To talk to the C906 from your own Linux program, copy what `bl_send.c`
does: open `/dev/cvi-rtos-cmdqu`, fill a `cmdqu_t` (`ip_id` 6, your
`cmd_id`, `block` 1, `resv.mstime` = timeout, `param_ptr` = value), call
`ioctl(fd, RTOS_CMDQU_SEND_WAIT, &cmd)`, then judge the reply by its
content: the ioctl returns 0 even on timeout.

## Adding a command

1. Pick a free id in 0x00-0x4f (not 0x40) and `#define` it in `src/main.c`.
2. Call `bl_cmd_listen()` for it in `setup()`.
3. Add a `case` in `handle()` that checks the value, acts, and calls
   `bl_cmd_reply()`.
4. `pio run -t upload`, then `./bl_send.sh <id> <value>`. `bl_send` needs
   no change: it takes the id on the command line.

## If something goes wrong

- Exit 3 for every command: another program is running on the C906. Upload this one.
- `beitlabduino: ... refused` in the log: the message says why (pad or command id).
- To stop it: `ssh -t beitlab@<board> sudo -n /usr/libexec/playground/rtos-control recover` restarts the starter firmware. Linux stays up.

OS 0.4.0 uses `beitlab` and SSH keys. The one-time Linux tool installer and custom `bl_send` commands use interactive password-authenticated sudo; provision a user password with `--user-password`. The standard firmware upload/status/log/recover commands use the restricted passwordless helper.
