/* SPDX-License-Identifier: MIT */
/*
 * beitlab Robotics MilkV duo OS starter firmware.
 *
 * The program a fresh card runs on the C906L, and the one
 * `rtos-agent recover` returns to. Installed in the image as
 * /lib/firmware/playground.elf and /lib/firmware/playground-starter.elf.
 *
 * - Prints "playground starter <version>" once at start. On UART0 the line
 *   carries the vendor "RT: " prefix; `rtos-agent log` shows it too.
 * - Answers the playground ping (playground_cmd.h): nonce -> nonce + 1.
 * - Touches no GPIO, pinmux, PWM, SPI or I2C. The Duo S LED is not toggled:
 *   the SDK's u-boot board init muxes it on XGPIOA_29 (IIC0_SDA pad), but
 *   no schematic in this repository shows that the pad is free of the
 *   carrier sockets, so PROMPT.md requires the starter to toggle nothing.
 */
/* stdint first: the SDK FreeRTOS.h does not include it (vendor sources
 * get the types from <stdio.h> via newlib). */
#include <stdint.h>
#include "FreeRTOS.h"
#include "task.h"
#include "playground.h"

static QueueHandle_t ping_queue;

static void starter_task(void *arg)
{
	cmdqu_t cmd;

	(void)arg;
	pg_print("playground starter %s", PLAYGROUND_VERSION);
	for (;;) {
		if (xQueueReceive(ping_queue, &cmd, portMAX_DELAY) != pdTRUE)
			continue;
		if (pg_cmd_reply(&cmd, cmd.param_ptr + 1) == 0)
			pg_print("starter ping %u -> %u", cmd.param_ptr, cmd.param_ptr + 1);
		else
			pg_print("starter ping %u: mailbox busy", cmd.param_ptr);
	}
}

void playground_app_init(void)
{
	ping_queue = pg_cmd_subscribe(PLAYGROUND_CMD_PING);
	xTaskCreate(starter_task, "starter", configMINIMAL_STACK_SIZE, NULL,
		    tskIDLE_PRIORITY + 2, NULL);
}
