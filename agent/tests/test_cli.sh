#!/bin/sh
# SPDX-License-Identifier: MIT
# CLI test of rtos-agent against a fake root (sysfs, /lib/firmware, /etc).
# The test build emulates the remoteproc "state" attribute: "start" boots
# unless the firmware contains FAILSTART. Run through `make test`.
set -u
AGENT="$1"
HERE="$(cd "$(dirname "$0")" && pwd)"
EMIT="$(dirname "$AGENT")/test_unit"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
export RTOS_AGENT_ROOT="$T" RTOS_AGENT_FAKE_ROOT=1
B=$PG_C906L_BOOT_ADDR; R=$PG_RTOS_BASE; S=$PG_RTOS_SIZE
fail=0
ok()   { echo "ok: $*"; }
bad()  { echo "FAIL: $*"; fail=1; }
expect_rc() { want=$1; shift; out="$("$@" 2>&1)"; rc=$?
  if [ $rc -eq "$want" ]; then ok "$* -> $rc"; else bad "$* -> $rc (want $want): $out"; fi; }
state() { cat "$T/sys/class/remoteproc/remoteproc0/state"; }
fwattr() { cat "$T/sys/class/remoteproc/remoteproc0/firmware"; }

R0="$T/sys/class/remoteproc/remoteproc0"
mkdir -p "$R0" "$T/lib/firmware" "$T/etc" "$T/run" "$T/dev"
printf running > "$R0/state"; printf playground.elf > "$R0/firmware"
"$EMIT" emit good "$T/lib/firmware/playground.elf" "$B" "$R" "$S"
cp "$T/lib/firmware/playground.elf" "$T/lib/firmware/playground-starter.elf"
cat > "$T/etc/duo-playground-release" <<EOF
DUO_PLAYGROUND_VERSION=9.9.9
SDK_COMMIT=test
EOF
"$EMIT" emit good "$T/hello.elf" "$B" "$R" "$S"
"$EMIT" emit failstart "$T/broken.elf" "$B" "$R" "$S"
echo "not an elf" > "$T/text.txt"

# boot sets the factory lock state
expect_rc 0 "$AGENT" boot
[ "$(cat "$T/etc/rtos-agent/lock")" = "unlocked" ] && ok "boot wrote lock=unlocked" || bad "lock default"

out="$("$AGENT" status)"
echo "$out" | grep -qx 'state=running' && echo "$out" | grep -qx 'firmware=/lib/firmware/playground.elf' \
  && echo "$out" | grep -qx 'lock=unlocked' && echo "$out" | grep -qx 'release=9.9.9' \
  && ok "status block" || bad "status block: $out"

# bad file: exit 2, live firmware untouched, still running
cp "$T/lib/firmware/playground.elf" "$T/before.elf"
expect_rc 2 "$AGENT" load "$T/text.txt"
cmp -s "$T/before.elf" "$T/lib/firmware/playground.elf" && ok "bad file did not replace firmware" || bad "firmware replaced"
[ "$(state)" = running ] && ok "still running after bad file" || bad "state $(state)"
[ ! -e "$T/lib/firmware/playground.elf.new" ] && ok "no .new left behind" || bad ".new left"

# good load
printf 'x' >> "$T/hello.elf"   # trailing byte: still a valid ELF, different hash
expect_rc 0 "$AGENT" load "$T/hello.elf"
cmp -s "$T/hello.elf" "$T/lib/firmware/playground.elf" && ok "firmware replaced" || bad "firmware not replaced"
[ "$(state)" = running ] && [ "$(fwattr)" = playground.elf ] && ok "running playground.elf" || bad "state $(state) fw $(fwattr)"

# lock
expect_rc 0 "$AGENT" lock
"$AGENT" status | grep -qx 'lock=locked' && ok "status shows locked" || bad "lock status"
out="$("$AGENT" load "$T/hello.elf")"; rc=$?
[ $rc -eq 3 ] && [ "$out" = locked ] && ok "load while locked -> 3 'locked'" || bad "locked load rc=$rc out=$out"
expect_rc 3 "$AGENT" recover
expect_rc 0 "$AGENT" unlock
expect_rc 0 "$AGENT" load "$T/hello.elf"

# start failure: exit 4, starter restarted, starter file kept
expect_rc 4 "$AGENT" load "$T/broken.elf"
[ "$(fwattr)" = playground-starter.elf ] && [ "$(state)" = running ] && ok "fell back to starter" || bad "fallback fw $(fwattr) $(state)"
[ -f "$T/lib/firmware/playground-starter.elf" ] && ok "starter kept" || bad "starter deleted"

# recover
expect_rc 0 "$AGENT" load "$T/hello.elf"
expect_rc 0 "$AGENT" recover
[ "$(fwattr)" = playground-starter.elf ] && ok "recover runs starter" || bad "recover fw $(fwattr)"
cmp -s "$T/lib/firmware/playground-starter.elf" "$T/before.elf" && ok "starter content unchanged" || bad "starter changed"

# root check
if [ "$(id -u)" -ne 0 ]; then
  RTOS_AGENT_FAKE_ROOT= expect_rc 1 env -u RTOS_AGENT_FAKE_ROOT "$AGENT" load "$T/hello.elf"
fi

# log without /dev/cvi-fast-image: explains the serial console
out="$("$AGENT" log 2>&1)"; rc=$?
[ $rc -eq 1 ] && echo "$out" | grep -q '/dev/ttyS0' && ok "log fallback names /dev/ttyS0" || bad "log fallback: $out"

# missing remoteproc0
rm -rf "$R0"
out="$("$AGENT" status 2>/dev/null)"; rc=$?
[ $rc -eq 1 ] && echo "$out" | grep -qx 'state=faulted' && ok "missing remoteproc0 -> faulted, rc 1" || bad "missing: rc=$rc $out"
expect_rc 1 "$AGENT" boot

[ $fail -eq 0 ] && echo "PASSED: test_cli" || echo "FAILED: test_cli"
exit $fail
