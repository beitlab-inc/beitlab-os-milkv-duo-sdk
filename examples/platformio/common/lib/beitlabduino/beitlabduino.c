/* SPDX-License-Identifier: MIT */
/* beitlabduino runtime: runs setup()/loop(), drives GPIO pads safely and
 * passes mailbox commands from Linux. */
#include <stdint.h>
#include "FreeRTOS.h"
#include "task.h"
#include "queue.h"
#include "beitlabduino.h"

static struct {
	uint8_t id;
	QueueHandle_t queue;
} listens[PG_MAX_SUBSCRIPTIONS];
static unsigned nlistens;

int bl_pin_mode(const char *pad, int mode)
{
	if (mode != BL_OUTPUT) {
		bl_print("beitlabduino: only BL_OUTPUT is supported");
		return -1;
	}
	return pg_gpio_output(pad);
}

void bl_write(const char *pad, int level)
{
	(void)pg_gpio_write(pad, level);
}

/* ms to ticks, rounded up (pdMS_TO_TICKS rounds down: pdMS_TO_TICKS(1)
 * is 0 at 200 Hz). */
static TickType_t ms_to_ticks(uint32_t ms)
{
	return (TickType_t)(((uint64_t)ms * configTICK_RATE_HZ + 999) / 1000);
}

void bl_delay(uint32_t ms)
{
	vTaskDelay(ms_to_ticks(ms));
}

int bl_cmd_listen(uint8_t id)
{
	QueueHandle_t q;
	unsigned i;

	if (id >= PLAYGROUND_VENDOR_SYS_FIRST || id == PLAYGROUND_CMD_PING) {
		bl_print("beitlabduino: cmd 0x%x refused: "
			 "use 0x00..0x4f, not 0x%x (ping)", id, PLAYGROUND_CMD_PING);
		return -1;
	}
	for (i = 0; i < nlistens; i++)
		if (listens[i].id == id)
			return 0;
	/* playground.h asks for pg_cmd_subscribe() in playground_app_init(),
	 * before the runtime's command task runs. setup() runs later, in a
	 * task, so stop task switches while the runtime's table changes. The
	 * mailbox interrupt does not read that table. */
	vTaskSuspendAll();
	q = pg_cmd_subscribe(id);
	(void)xTaskResumeAll();
	if (!q) {
		bl_print("beitlabduino: cmd 0x%x refused: at most %d commands",
			 id, PG_MAX_SUBSCRIPTIONS);
		return -1;
	}
	listens[nlistens].id = id;
	listens[nlistens].queue = q;
	nlistens++;
	return 0;
}

int bl_cmd_receive(bl_cmd_t *cmd, uint32_t wait_ms)
{
	TickType_t start = xTaskGetTickCount();
	TickType_t wait = ms_to_ticks(wait_ms);
	unsigned i;

	for (;;) {
		for (i = 0; i < nlistens; i++) {
			if (xQueueReceive(listens[i].queue, &cmd->raw, 0) == pdTRUE) {
				cmd->id = cmd->raw.cmd_id;
				cmd->value = cmd->raw.param_ptr;
				return 1;
			}
		}
		if (xTaskGetTickCount() - start >= wait)
			return 0;
		vTaskDelay(1);  /* look again on the next tick */
	}
}

int bl_cmd_reply(const bl_cmd_t *cmd, uint32_t value)
{
	return pg_cmd_reply(&cmd->raw, BL_CMD_ACK | (value & ~BL_CMD_ACK));
}

static void bl_task(void *arg)
{
	(void)arg;
	setup();
	for (;;)
		loop();
}

void playground_app_init(void)
{
	xTaskCreate(bl_task, "beitlabduino", configMINIMAL_STACK_SIZE, NULL,
		    tskIDLE_PRIORITY + 1, NULL);
}
