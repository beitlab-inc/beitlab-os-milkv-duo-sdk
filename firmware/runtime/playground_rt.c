/* SPDX-License-Identifier: MIT */
/*
 * beitlab Robotics MilkV duo OS C906L runtime: main_cvirtos(), mailbox command task,
 * console/log output. See firmware/include/playground.h.
 *
 * The mailbox code follows freertos/cvitek/task/comm/src/riscv64/
 * comm_main.c at the sdk.lock commit (prvQueueISR / prvCmdQuRunTask
 * send_label), with these differences, each recorded in docs/sdk-notes.md:
 *
 *  - Replies always go to SEND_TO_CPU0 (the Cortex-A53). The vendor code
 *    picks CPU0 or CPU1 from transfer_config.conf_magic, which FSBL writes
 *    into mailbox RAM at power-on. After a remoteproc reload that block is
 *    gone, GetCommInfo() falls back to RTOS_MAGIC_HEADER, which is
 *    C906_MAGIC_HEADER on __riscv, and replies would go to the big C906.
 *    This image is ARM-only, so CPU0 is the only right answer.
 *  - transfer_config.conf_magic is set back to CA53_MAGIC_HEADER for the
 *    same reason, because Linux fast_image checks it.
 *  - The mailbox register pointers are set before the IRQ is requested
 *    (the vendor sets them inside the task, after request_irq()).
 *  - Only IP_SYSTEM is routed; the playground has no ISP/VI/VCODEC tasks.
 */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "FreeRTOS.h"
#include "task.h"
#include "queue.h"

#include "mmio.h"
#include "irq.h"
#include "intr_conf.h"
#include "top_reg.h"
#include "cvi_mailbox.h"
#include "cvi_spinlock.h"
#include "rtos_cmdqu.h"
#include "fast_image.h"
#include "dump_uart.h"
#include "uart.h"

#include "playground.h"

/* Defined by the vendor driver library (driver/common/src/system.c and
 * driver/uart/src/uart.c). */
extern struct transfer_config_t transfer_config;
extern struct dump_uart_s *dump_uart;
extern hw_raw_spinlock_t printf_lock;

static volatile struct mailbox_set_register *pg_mbox_reg;
static volatile unsigned long *pg_mbox_buf; /* 8 slots of cmdqu_t */
static DEFINE_CVI_SPINLOCK(pg_mailbox_lock, SPIN_MBOX);
static QueueHandle_t pg_cmd_queue;

static struct {
	uint8_t cmd_id;
	QueueHandle_t queue;
} pg_subs[PG_MAX_SUBSCRIPTIONS];
static int pg_nsubs;

/* ------------------------------------------------------------------ */
/* Output                                                              */

static void ring_putc(char c)
{
	char *buf = (char *)(uintptr_t)dump_uart->dump_uart_ptr;

	buf[dump_uart->dump_uart_pos++] = c;
	if (dump_uart->dump_uart_pos >= dump_uart->dump_uart_max_size) {
		dump_uart->dump_uart_overflow = 1;
		dump_uart->dump_uart_pos = 0;
	}
}

static void ring_puts(const char *s)
{
	for (; *s; s++) {
		if (*s == '\n')
			ring_putc('\r');
		ring_putc(*s);
	}
}

void pg_print(const char *fmt, ...)
{
	char line[PLAYGROUND_PRINT_MAX + 2];
	va_list ap;
	size_t len;
	int flags;

	va_start(ap, fmt);
	vsnprintf(line, PLAYGROUND_PRINT_MAX, fmt, ap);
	va_end(ap);
	line[PLAYGROUND_PRINT_MAX] = '\0';
	len = strlen(line);
	if (len == 0 || line[len - 1] != '\n') {
		line[len++] = '\n';
		line[len] = '\0';
	}

	/* Same lock the vendor uart_put_buff() takes (SPIN_UART). */
	drv_spin_lock_irqsave(&printf_lock, flags);
	uart_puts("RT: ");
	uart_puts(line);
	if (dump_uart && dump_uart->dump_uart_enable) {
		ring_puts("RTD: ");
		ring_puts(line);
	}
	if (flags != MAILBOX_LOCK_FAILED)
		drv_spin_unlock_irqrestore(&printf_lock, flags);
}

/* ------------------------------------------------------------------ */
/* Mailbox                                                             */

static int pg_mailbox_send(cmdqu_t *cmdq)
{
	volatile cmdqu_t *slot = (volatile cmdqu_t *)pg_mbox_buf;
	int flags, valid;

	drv_spin_lock_irqsave(&pg_mailbox_lock, flags);
	if (flags == MAILBOX_LOCK_FAILED)
		return -1;

	for (valid = 0; valid < MAILBOX_MAX_NUM; valid++, slot++) {
		if (slot->resv.valid.linux_valid == 0 && slot->resv.valid.rtos_valid == 0) {
			/* Mailbox buffer is written 4 bytes at a time (vendor note). */
			volatile int *word = (volatile int *)slot;

			cmdq->resv.valid.rtos_valid = 1;
			*word = (cmdq->ip_id << 0) | (cmdq->cmd_id << 8) | (cmdq->block << 15) |
				(cmdq->resv.valid.linux_valid << 16) |
				(cmdq->resv.valid.rtos_valid << 24);
			slot->param_ptr = cmdq->param_ptr;
			pg_mbox_reg->cpu_mbox_set[SEND_TO_CPU0].cpu_mbox_int_clr.mbox_int_clr = (1 << valid);
			pg_mbox_reg->cpu_mbox_en[SEND_TO_CPU0].mbox_info |= (1 << valid);
			pg_mbox_reg->mbox_set.mbox_set = (1 << valid);
			break;
		}
	}
	drv_spin_unlock_irqrestore(&pg_mailbox_lock, flags);
	return valid < MAILBOX_MAX_NUM ? 0 : -1;
}

static int pg_mailbox_isr(int irqn, void *priv)
{
	BaseType_t yield = pdFALSE;
	unsigned char set_val;
	int i;

	(void)irqn;
	(void)priv;
	set_val = pg_mbox_reg->cpu_mbox_set[RECEIVE_CPU].cpu_mbox_int_int.mbox_int;
	for (i = 0; i < MAILBOX_MAX_NUM; i++) {
		unsigned char bit = set_val & (1 << i);
		volatile unsigned long *slot = pg_mbox_buf + i;
		cmdqu_t cmdq;

		if (!bit)
			continue;
		pg_mbox_reg->cpu_mbox_set[RECEIVE_CPU].cpu_mbox_int_clr.mbox_int_clr = bit;
		pg_mbox_reg->cpu_mbox_en[RECEIVE_CPU].mbox_info &= ~bit;
		*((unsigned long *)&cmdq) = *slot;
		*slot = 0;
		if (cmdq.resv.valid.linux_valid == 1 && cmdq.ip_id == IP_SYSTEM)
			xQueueSendFromISR(pg_cmd_queue, &cmdq, &yield);
	}
	portYIELD_FROM_ISR(yield);
	return 0;
}

QueueHandle_t pg_cmd_subscribe(uint8_t cmd_id)
{
	QueueHandle_t q;

	if (cmd_id >= PLAYGROUND_VENDOR_SYS_FIRST || pg_nsubs >= PG_MAX_SUBSCRIPTIONS)
		return NULL;
	q = xQueueCreate(4, sizeof(cmdqu_t));
	if (!q)
		return NULL;
	pg_subs[pg_nsubs].cmd_id = cmd_id;
	pg_subs[pg_nsubs].queue = q;
	pg_nsubs++;
	return q;
}

int pg_cmd_reply(const cmdqu_t *request, uint32_t param)
{
	cmdqu_t reply = *request;

	reply.param_ptr = param;
	return pg_mailbox_send(&reply);
}

static void pg_cmd_task(void *arg)
{
	cmdqu_t cmdq;
	int i;

	(void)arg;
	transfer_config.mcu_status = MCU_STATUS_RTOS_T1_RUNNING;
	for (;;) {
		xQueueReceive(pg_cmd_queue, &cmdq, portMAX_DELAY);
		switch (cmdq.cmd_id) {
		case SYS_CMD_INFO_LINUX_INIT_DONE:
			cmdq.cmd_id = SYS_CMD_INFO_RTOS_INIT_DONE;
			cmdq.param_ptr = (unsigned int)(uintptr_t)&transfer_config;
			pg_mailbox_send(&cmdq);
			break;
		case SYS_CMD_INFO_DUMP_MSG:
			cmdq.param_ptr = (unsigned int)(uintptr_t)dump_uart_msg();
			pg_mailbox_send(&cmdq);
			break;
		case SYS_CMD_INFO_DUMP_EN:
			dump_uart_enable();
			break;
		case SYS_CMD_INFO_DUMP_DIS:
			dump_uart_disable();
			break;
		default:
			for (i = 0; i < pg_nsubs; i++) {
				if (pg_subs[i].cmd_id == cmdq.cmd_id &&
				    xQueueSend(pg_subs[i].queue, &cmdq, 0) == pdTRUE)
					break;
			}
			/* Unclaimed: echo, like the vendor default case, so a waiting
			 * Linux caller returns instead of timing out. */
			if (i == pg_nsubs)
				pg_mailbox_send(&cmdq);
			break;
		}
	}
}

/* ------------------------------------------------------------------ */
/* Entry, called by the vendor task/main main()                        */

static void pg_fix_transfer_config(void)
{
	unsigned char *p = (unsigned char *)&transfer_config;
	unsigned short sum = 0;
	uint32_t i;

	if (transfer_config.conf_magic == CA53_MAGIC_HEADER)
		return;
	transfer_config.conf_magic = CA53_MAGIC_HEADER;
	for (i = 0; i < transfer_config.conf_size; i++)
		sum += p[i];
	transfer_config.checksum = sum;
}

void main_cvirtos(void)
{
	pg_mbox_reg = (volatile struct mailbox_set_register *)MAILBOX_REG_BASE;
	pg_mbox_buf = (volatile unsigned long *)MAILBOX_REG_BUFF;

	pg_fix_transfer_config();
	/* Keep the vendor dump ring on so `rtos-agent log` has data in both
	 * boot paths (FSBL handoff or remoteproc reload). pg_print() still
	 * writes every line to UART0. */
	if (dump_uart)
		dump_uart_enable();

	cvi_spinlock_init();
	pg_cmd_queue = xQueueCreate(16, sizeof(cmdqu_t));
	xTaskCreate(pg_cmd_task, "pg_cmdqu", configMINIMAL_STACK_SIZE, NULL,
		    tskIDLE_PRIORITY + 5, NULL);

	playground_app_init();

	request_irq(MBOX_INT_C906_2ND, pg_mailbox_isr, 0, "mailbox", NULL);
	vTaskStartScheduler();
	for (;;)
		;
}
