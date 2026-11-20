/* SPDX-License-Identifier: MIT */
/* Carrier revision 0.11: inspect reservations and blink only the Duo S LED.
 * Reservation probes never initialize or write a carrier pad. */
#include <stddef.h>
#include <string.h>
#include "beitlabduino.h"

static int led_ready;
static int level;

static int show_reservation(const char *pad)
{
	const char *reason = pg_profile_gpio_output(pad, NULL);

	if (!reason) {
		bl_print("robotics board idle: %s unexpectedly allowed", pad);
		return 0;
	}
	bl_print("%s protected: %s", pad, reason);
	return 1;
}

void setup(void)
{
	bl_print("robotics board profile %s", pg_profile_name());
	if (strcmp(pg_profile_name(), "beitlab_robotics_rev011")) {
		bl_print("robotics board idle: select beitlab_robotics_rev011");
		return;
	}
	/* B19 is MOT_EN_REQ; A18 is the left knee servo PWM. */
	if (!show_reservation("B19") || !show_reservation("A18"))
		return;
	bl_print("I2C4 reserved: %s", pg_profile_resource("i2c4"));
	led_ready = bl_pin_mode(BL_LED, BL_OUTPUT) == 0;
	if (led_ready)
		bl_print("robotics board heartbeat on %s", BL_LED);
}

void loop(void)
{
	if (led_ready) {
		level = !level;
		bl_write(BL_LED, level);
	}
	bl_delay(500);
}
