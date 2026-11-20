/* SPDX-License-Identifier: MIT */
/*
 * heartbeat: toggle one GPIO at 1 Hz (500 ms high, 500 ms low).
 * The pin comes from ../pin.h. With PLAYGROUND_GPIO_ENABLE 0 (the shipped
 * state) nothing is toggled and the program prints "heartbeat idle".
 *
 * The robot firmware for the carrier is ../Balanced_robot; this example
 * does not use it and must not drive any pad it owns.
 */
/* stdint first: the SDK FreeRTOS.h does not include it (vendor sources
 * get the types from <stdio.h> via newlib). */
#include <stdint.h>
#include "FreeRTOS.h"
#include "task.h"
#include "playground.h"
#include "pg_gpio.h"
#include "pin.h"

#if PLAYGROUND_GPIO_ENABLE
_Static_assert(sizeof(PLAYGROUND_GPIO_PAD) > 1,
	       "PLAYGROUND_GPIO_ENABLE is 1 but PLAYGROUND_GPIO_PAD is empty: see pin.h");
#endif

static void heartbeat_task(void *arg)
{
	const char *pad = PLAYGROUND_GPIO_PAD;
	int level = 0;

	(void)arg;
	if (!PLAYGROUND_GPIO_ENABLE) {
		pg_print("heartbeat idle (PLAYGROUND_GPIO_ENABLE is 0 in pin.h)");
		vTaskDelete(NULL);
		return;
	}
	if (pg_gpio_output(pad)) {
		vTaskDelete(NULL);
		return;
	}
	pg_print("heartbeat on %s [%s], 1 Hz", pad, pg_profile_name());

	for (;;) {
		vTaskDelay(pdMS_TO_TICKS(500));
		level = !level;
		(void)pg_gpio_write(pad, level);
	}
}

void playground_app_init(void)
{
	xTaskCreate(heartbeat_task, "heartbeat", configMINIMAL_STACK_SIZE, NULL,
		    tskIDLE_PRIORITY + 1, NULL);
}
