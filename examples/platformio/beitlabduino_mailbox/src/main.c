/* SPDX-License-Identifier: MIT */
/*
 * beitlabduino_mailbox: Linux controls the on-board LED through the mailbox.
 *
 *   cmd 0x41  LED mode       value 0 off, 1 on, 2 blink   reply: new mode
 *   cmd 0x42  blink period   value 50..5000 (ms per half) reply: new period
 *
 * Any other value is refused: the reply is BL_CMD_ERROR. From Linux on
 * the board: bl_send 0x41 1 (linux/bl_send.c).
 */
#include "beitlabduino.h"

#define CMD_LED    0x41
#define CMD_PERIOD 0x42

enum { LED_OFF, LED_ON, LED_BLINK };

static uint32_t mode = LED_BLINK;
static uint32_t period_ms = 500;
static int level;

static void set_led(int on)
{
	level = on;
	bl_write(BL_LED, on ? BL_HIGH : BL_LOW);
}

static void handle(const bl_cmd_t *cmd)
{
	switch (cmd->id) {
	case CMD_LED:
		if (cmd->value > LED_BLINK)
			break;
		mode = cmd->value;
		if (mode != LED_BLINK)
			set_led(mode == LED_ON);
		bl_print("led mode %d", (int)mode);
		bl_cmd_reply(cmd, mode);
		return;
	case CMD_PERIOD:
		if (cmd->value < 50 || cmd->value > 5000)
			break;
		period_ms = cmd->value;
		bl_print("blink period %d ms", (int)period_ms);
		bl_cmd_reply(cmd, period_ms);
		return;
	}
	bl_print("cmd 0x%x value %d refused", cmd->id, (int)cmd->value);
	bl_cmd_reply(cmd, BL_CMD_ERROR);
}

void setup(void)
{
	bl_pin_mode(BL_LED, BL_OUTPUT);
	bl_cmd_listen(CMD_LED);
	bl_cmd_listen(CMD_PERIOD);
	bl_print("beitlabduino mailbox on %s, commands 0x%x 0x%x",
		 BL_LED, CMD_LED, CMD_PERIOD);
}

void loop(void)
{
	bl_cmd_t cmd;

	/* Wait one blink step, but wake at once when a command arrives. */
	if (bl_cmd_receive(&cmd, period_ms)) {
		handle(&cmd);
		return;
	}
	if (mode == LED_BLINK)
		set_led(!level);
}
