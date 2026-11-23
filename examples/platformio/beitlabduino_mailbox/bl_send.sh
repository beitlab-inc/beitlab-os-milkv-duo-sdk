#!/bin/sh
# SPDX-License-Identifier: MIT
# Send one beitlabduino command to the C906 and print the reply.
#
# On the Duo S:   bl_send.sh <cmd_id> [value] [timeout_ms]   (uses /usr/bin/bl_send)
# From the PC:    ./bl_send.sh <cmd_id> [value] [timeout_ms] (runs it over SSH,
#                 through ../common/duo_ssh.py, so PIO_DUO_HOST/PIO_DUO_PASS work
#                 as they do for upload)
#
# bl_send is linux/bl_send.c; install it once with: python3 linux/install.py
set -eu
if [ -e /dev/cvi-rtos-cmdqu ]; then
  exec bl_send "$@"
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$HERE/../common/duo_ssh.py" bl_send "$@"
