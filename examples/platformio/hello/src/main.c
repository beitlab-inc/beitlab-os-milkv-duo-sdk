/* SPDX-License-Identifier: MIT */
/* hello: one FreeRTOS task prints "playground hello <build-id>" every
 * second. <build-id> is the compile date and time. No GPIO. */
/* stdint first: the SDK FreeRTOS.h does not include it (vendor sources
 * get the types from <stdio.h> via newlib). */
#include <stdint.h>
#include "FreeRTOS.h"
#include "task.h"
#include "playground.h"

#define BUILD_ID __DATE__ " " __TIME__

static void hello_task(void *arg)
{
	(void)arg;
	for (;;) {
		pg_print("playground beitlab milkb duo hello %s", BUILD_ID);
		vTaskDelay(pdMS_TO_TICKS(1000));
	}
}

void playground_app_init(void)
{
	xTaskCreate(hello_task, "hello", configMINIMAL_STACK_SIZE, NULL,
		    tskIDLE_PRIORITY + 1, NULL);
}
