/* beitlabduino_blink: blink the Duo S on-board LED, 500 ms on, 500 ms off. */
#include "beitlabduino.h"

void setup(void)
{
	bl_pin_mode(BL_LED, BL_OUTPUT);
	bl_print("beitlabduino blink on %s", BL_LED);
}

void loop(void)
{
	bl_write(BL_LED, BL_HIGH);
	bl_delay(500);
	bl_write(BL_LED, BL_LOW);
	bl_delay(500);
}
