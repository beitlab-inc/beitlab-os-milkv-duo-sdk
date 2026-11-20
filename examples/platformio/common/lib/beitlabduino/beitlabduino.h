/* SPDX-License-Identifier: MIT */
/*
 * beitlabduino: a small setup()/loop() layer for C906 programs.
 *
 * Your program defines setup() and loop(). beitlabduino runs setup() once,
 * then loop() forever, in one FreeRTOS task. loop() must wait, with
 * bl_delay() or bl_cmd_receive(), or the task never lets the CPU rest.
 *
 * Pins are Duo S pad names ("A29"). custom_profile in platformio.ini selects
 * their permissions; the default protects the robotics carrier. Only verified
 * outputs are accepted. See docs/PROFILES.md.
 *
 * To use it, add one line to the example's [env:...] in platformio.ini:
 *   custom_libs = beitlabduino
 * and #include "beitlabduino.h". Do not define playground_app_init():
 * beitlabduino does, and calls your setup() and loop().
 */
#ifndef BEITLABDUINO_H
#define BEITLABDUINO_H

#include <stdint.h>
#include "FreeRTOS.h"
#include "playground.h"
#include "pg_gpio.h"

/* Duo S on-board LED (U-Boot muxes IIC0_SDA to XGPIOA_29 as "LED"). */
#define BL_LED "A29"

#define BL_OUTPUT 1
#define BL_LOW    0
#define BL_HIGH   1

/* Written by you. */
void setup(void);
void loop(void);

/* Make pad an output, driven low. Only BL_OUTPUT is supported.
 * Returns 0, or -1 (and prints why) if the pad is refused. */
int bl_pin_mode(const char *pad, int mode);

/* Drive an output pad BL_HIGH or BL_LOW. Pads that bl_pin_mode() did not
 * accept are ignored. */
void bl_write(const char *pad, int level);

/* Wait about ms milliseconds. The tick is 5 ms: ms is rounded up to
 * whole ticks, and the first tick may already be partly over, so the wait
 * can be up to 5 ms shorter (bl_delay(1) returns at the next tick). */
void bl_delay(uint32_t ms);

/* Print one line, from setup() or loop(). It is pg_print(), whose
 * formatting is the vendor vsnprintf (common/src/riscv64/snprintf.c), not
 * newlib's. It knows only %d %x %s %c %p, with l (%ld %lx):
 *   - %x and %p always print every hex digit (0x41 -> "00000041");
 *     widths such as %02x are ignored.
 *   - %u, %i, %f and the rest are NOT supported. The letter does not end
 *     the conversion, so the next d/x/s/c/p in the text is taken as one:
 *     "period %u ms" reads the number as a string pointer at the "s" of
 *     "ms" and crashes the C906. Use %d (values below 2^31) or %x. */
#define bl_print pg_print

/* ---- Mailbox commands from Linux ------------------------------------
 *
 * Linux sends an 8-byte message: a command id and a 32-bit value
 * (bl_send on the board, see examples/platformio/beitlabduino_mailbox). Linux
 * waits for one reply with the same id.
 *
 * Every reply has BL_CMD_ACK (bit 31) set. That is how Linux tells a real
 * reply from the runtime's echo: a command nobody listens for is sent
 * back unchanged, and Linux never sends bit 31. So reply values have 31
 * bits; BL_CMD_ERROR is the conventional "refused" value.
 */
#define BL_CMD_ACK   0x80000000u
#define BL_CMD_ERROR 0x7fffffffu

typedef struct {
	uint8_t id;      /* command id, 0x00..0x4f */
	uint32_t value;  /* the value Linux sent */
	cmdqu_t raw;     /* the message itself, kept for the reply */
} bl_cmd_t;

/* Deliver command id to bl_cmd_receive(). Call from setup(). Ids
 * 0x00..0x4f except 0x40 (PLAYGROUND_CMD_PING); at most
 * PG_MAX_SUBSCRIPTIONS ids. Returns 0, or -1 (and prints why). */
int bl_cmd_listen(uint8_t id);

/* Wait up to wait_ms for a command on any listened id. Returns 1 and
 * fills cmd as soon as one arrives (within one 5 ms tick), else 0 when
 * the time is up. It counts as a delay for loop(), unless wait_ms is 0. */
int bl_cmd_receive(bl_cmd_t *cmd, uint32_t wait_ms);

/* Answer cmd with value (31 bits; BL_CMD_ACK is added). Reply exactly
 * once to every command, soon: Linux waits 1 s by default. Returns 0, or
 * -1 if the mailbox had no free slot. */
int bl_cmd_reply(const bl_cmd_t *cmd, uint32_t value);

#endif
