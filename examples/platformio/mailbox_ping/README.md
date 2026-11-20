# mailbox_ping

Profile: `duos_bare`, explicitly selected in `platformio.ini` for a Duo S without the carrier.

What it does: a task on the C906 waits for mailbox command `0x40`. It replies with the nonce plus one. The id is defined in `common/playground_cmd.h`. How the mailbox works: `docs/mailbox.md`.

Upload, from this folder: `pio run -t upload`

Test from the PC: `./host_ping.sh 7`. On the board, the same test is `host_ping 7`.

You should see `host_ping: reply 8 == 7 + 1, ok`. The command exits with 0.

The monitor (`pio run -t rtmonitor`) shows `mailbox_ping nonce=7 reply=8`.

Exit 3 means the reply came back unchanged: a program that does not answer ping is running, for example `hello`.
