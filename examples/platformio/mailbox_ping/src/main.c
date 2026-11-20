/* SPDX-License-Identifier: MIT */
/* mailbox_ping: a task waits for PLAYGROUND_CMD_PING (0x40) and replies
 * with nonce + 1, the same contract as the starter (playground_cmd.h).
 * Test it from Linux with host_ping.sh. No GPIO. */
/* stdint first: the SDK FreeRTOS.h does not include it (vendor sources
 * get the types from <stdio.h> via newlib). */
#include <stdint.h>
#include "FreeRTOS.h"
#include "task.h"
#include "playground.h"
#include "playground_cmd.h"

static QueueHandle_t ping_queue;

static void ping_task(void *arg)
{
	cmdqu_t cmd;

	(void)arg;
	pg_print("playground mailbox_ping %s %s, waiting for cmd 0x%02x",
		 __DATE__, __TIME__, PLAYGROUND_CMD_PING);
	for (;;) {
		if (xQueueReceive(ping_queue, &cmd, portMAX_DELAY) != pdTRUE)
			continue;
		if (pg_cmd_reply(&cmd, cmd.param_ptr + 1) == 0)
			pg_print("mailbox_ping nonce=%u reply=%u", cmd.param_ptr, cmd.param_ptr + 1);
		else
			pg_print("mailbox_ping nonce=%u: no free mailbox slot", cmd.param_ptr);
	}
}

void playground_app_init(void)
{
	ping_queue = pg_cmd_subscribe(PLAYGROUND_CMD_PING);
	xTaskCreate(ping_task, "ping", configMINIMAL_STACK_SIZE, NULL,
		    tskIDLE_PRIORITY + 2, NULL);
}
