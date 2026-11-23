# The mailbox between Linux and the C906

Linux (on the A53) and the program on the C906 talk through the vendor
command queue: an 8-byte message in a shared mailbox slot, plus an
interrupt. This page explains how a message travels, which ids a program
may use, and how to send one from Linux.

Two examples use it:

- `examples/platformio/mailbox_ping`: the ping contract the starter
  firmware also answers. Linux tool: `host_ping`, in the image.
- `examples/platformio/beitlabduino_mailbox`: LED commands written with
  the beitlabduino library. Linux tool: `bl_send`, installed from the PC.

## How a message travels

```
 Linux (A53)                          C906 (FreeRTOS)
 ───────────                          ───────────────
 host_ping / bl_send
   │ ioctl(SEND_WAIT) on /dev/cvi-rtos-cmdqu
   ▼
 kernel driver ──► mailbox slot + interrupt ──► runtime interrupt handler
   (sleeps, waiting                                │ queue
    for the reply)                                 ▼
                                    runtime command task
                                     ├─ vendor ids 0x50-0x5f: handled by the runtime
                                     ├─ an id the program listens for ─► its queue
                                     └─ any other id: sent back unchanged (echo)
                                                   │
                                    the program replies (pg_cmd_reply)
 driver wakes the caller ◄── mailbox slot + interrupt ◄┘
```

The C906 half is `firmware/runtime/playground_rt.c`: `pg_mailbox_isr()`
takes the message, `pg_cmd_task()` routes it, and `pg_cmd_reply()` sends
the answer. A program asks for an id with `pg_cmd_subscribe()` and gets
the messages on a FreeRTOS queue (`firmware/include/playground.h`).

## The message

`cmdqu_t`, copied from the pinned SDK (`rtos_cmdqu.h`, sha256 in
`sdk.lock`):

| Field | Request from Linux | Reply from the C906 |
|---|---|---|
| `ip_id` | 6 (`IP_SYSTEM`) | same |
| `cmd_id` (7 bits) | the command | same |
| `block` (1 bit) | 1 for `SEND_WAIT` | kept at 1 (Linux only wakes a waiter whose reply keeps it) |
| `resv` | `mstime`: how long Linux waits, in ms | `valid = {1, 1}` |
| `param_ptr` (32 bits) | the value | the answer |

`resv` is a union, so `valid = {1, 1}` reads as `mstime` 257. That is
why `host_ping` and `bl_send` refuse a timeout of 257 ms: with it, a
timed-out request could not be told from a reply.

## Command ids

| Ids | Use |
|---|---|
| 0x00-0x4f | free for programs |
| 0x10, 0x11 | board inspector (Robotics Board image starter): `PLAYGROUND_CMD_INSPECT_STATE`, `PLAYGROUND_CMD_INSPECT_FIELD`, read by `beitlab-board-status` |
| 0x12-0x17 | reserved for the board inspector |
| 0x40 | ping (`PLAYGROUND_CMD_PING`): the starter, `mailbox_ping`, `host_ping` |
| 0x41, 0x42 | used by `beitlabduino_mailbox` (LED mode, blink period) |
| 0x50-0x5f | vendor `SYS_CMD_ID`s, handled by the runtime; cannot be subscribed |
| 0x60 and up | rejected by the Linux driver |

The shared definitions are in `firmware/include/playground_cmd.h`.

## The echo, and how to tell a real reply

The runtime sends any command nobody listens for back unchanged, so a
waiting Linux caller returns at once instead of timing out. A reply equal
to the request therefore means "nobody answered". Each example makes its
real replies different from the request:

- `mailbox_ping` replies `nonce + 1`.
- beitlabduino sets bit 31 (`BL_CMD_ACK`) on every reply, and Linux
  never sends bit 31. Reply values therefore have 31 bits, and
  `BL_CMD_ERROR` (0x7fffffff) means "refused".

The direction is always Linux asks, C906 answers. Neither example sends
messages from the C906 on its own.

## Trying ping (mailbox_ping)

From `examples/platformio/mailbox_ping`:

```bash
pio run -t upload
./host_ping.sh 7          # from the PC, over SSH
```

On the board the same is `host_ping 7`. `host_ping [nonce] [timeout_ms]`
defaults to nonce 7 and 1000 ms. Expected:

```
host_ping: send ip=6 cmd=0x40 nonce=7
host_ping: reply 8 == 7 + 1, ok
```

and `mailbox_ping nonce=7 reply=8` in `pio run -t rtmonitor`. The starter
firmware answers ping too, so `host_ping` also works after
`rtos-agent recover`.

| `host_ping` exit | Meaning |
|---|---|
| 0 | reply == nonce + 1 |
| 1 | `/dev/cvi-rtos-cmdqu` could not be used |
| 2 | no reply within the timeout, or an unexpected value |
| 3 | nonce echoed back: the running program does not answer ping (`hello`, for example) |

## Sending from your own Linux program

Both tools do the same five steps; `examples/platformio/mailbox_ping/host_ping.c`
is the shortest to read.

1. `open("/dev/cvi-rtos-cmdqu", O_RDWR)`. The image udev rule grants
   root and the `beitlab` group access (owner/group `root:beitlab`, mode
   `0660`), so the board operator can run mailbox tools without sudo.
   See the [mailbox example](../examples/platformio/beitlabduino_mailbox/README.md)
   for the one-time update on older images.
2. Fill a `cmdqu_t`: `ip_id` 6, your `cmd_id`, `block` 1,
   `resv.mstime` = timeout in ms (not 257), `param_ptr` = value.
3. `ioctl(fd, RTOS_CMDQU_SEND_WAIT, &cmd)`.
4. Judge by content, because the ioctl returns 0 even on timeout: no
   reply if `resv.valid` is not `{1, 1}`; an echo if `param_ptr` is
   unchanged.
5. Otherwise `param_ptr` is the answer.

The board has no compiler. Build for aarch64 Linux on the PC with the SDK
host-tools compiler, static, as `scripts/build-image.sh` does for
`host_ping` and `beitlabduino_mailbox/linux/install.py` does for
`bl_send`.
