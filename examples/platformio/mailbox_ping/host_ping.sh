#!/bin/sh
# SPDX-License-Identifier: MIT
# Send the playground ping to the C906 and check the reply.
#
# On the Duo S:      host_ping.sh [nonce]     (uses /usr/bin/host_ping)
# From the PC:       ./host_ping.sh [nonce]   (runs it over SSH on
#                    $PIO_DUO_USER@$PIO_DUO_HOST, default beitlab@beitlab-milkv-duo.local,
#                    through ../common/duo_ssh.py, so PIO_DUO_PASS works as
#                    it does for upload)
#
# host_ping is host_ping.c in this folder, built into the image by
# scripts/build-image.sh. It talks to /dev/cvi-rtos-cmdqu with the cmdqu_t
# layout copied from the pinned rtos_cmdqu.h.
#
# Expected with mailbox_ping (or the starter) running, nonce 7:
#   host_ping: send ip=6 cmd=0x40 nonce=7
#   host_ping: reply 8 == 7 + 1, ok
set -eu
NONCE="${1:-7}"
if [ -e /dev/cvi-rtos-cmdqu ]; then
  if [ "$(id -u)" = 0 ]; then exec host_ping "$NONCE"; fi
  exec sudo -n /usr/libexec/playground/rtos-control ping "$NONCE"
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$HERE/../common/duo_ssh.py" host_ping "$NONCE"
