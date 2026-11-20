/* SPDX-License-Identifier: MIT */
/*
 * beitlab Robotics MilkV duo OS C906L runtime.
 *
 * Every playground ELF (starter and PlatformIO examples) links:
 *   - the vendor FreeRTOS libraries built from freertos/cvitek with the
 *     CVIRTOS run type (arch, kernel, common, hal, driver),
 *   - the vendor task/main sources (main(), vectors, tick),
 *   - firmware/runtime/playground_rt.c, which provides main_cvirtos(),
 *   - the program's own sources, which provide playground_app_init().
 *
 * The runtime replaces the vendor task/comm "CMDQU" task. It answers the
 * vendor IP_SYSTEM commands Linux sends (LINUX_INIT_DONE, DUMP_MSG,
 * DUMP_EN, DUMP_DIS) so /dev/cvi-fast-image keeps working after a
 * remoteproc reload, and hands playground commands to the program.
 *
 * ABI (docs/sdk-notes.md, "Firmware ABI"):
 *   - ELF64, EM_RISCV, little endian, float ABI double (lp64d), ET_EXEC.
 *   - e_entry == CVIMMAP_FREERTOS_ADDR (0x9fe00000): the remoteproc driver
 *     only toggles the C906L reset; the reset vector stays where FSBL put
 *     it.
 *   - every PT_LOAD inside [0x9fe00000, 0xa0000000).
 *   - no .resource_table: the driver does not require one.
 */
#ifndef PLAYGROUND_H
#define PLAYGROUND_H

#include <stdint.h>
#include "FreeRTOS.h"
#include "queue.h"
#include "rtos_cmdqu.h"
#include "playground_cmd.h"

#ifndef PLAYGROUND_VERSION
#define PLAYGROUND_VERSION "dev"
#endif

/* Implemented by each program. Called once from main_cvirtos() before the
 * scheduler starts: create tasks here. Do not block. */
void playground_app_init(void);

/* Print one line on both outputs the C906 has:
 *   - UART0 with the vendor "RT: " prefix (the Linux console; the line is
 *     interleaved with Linux output, which is expected), and
 *   - the vendor dump ring that `rtos-agent log` reads through
 *     /dev/cvi-fast-image.
 * A trailing newline is added if the text has none. Lines longer than
 * PLAYGROUND_PRINT_MAX are cut. Safe from tasks, not from ISRs.
 *
 * Formatting is the vendor vsnprintf (freertos/cvitek/common/src/riscv64/
 * snprintf.c), which replaces newlib's at link time. It supports only
 * %d %x %s %c %p and the l modifier; %x/%p print every hex digit and
 * widths are ignored. Any other conversion (%u, %i, %f, ...) does not end
 * the conversion: the next d/x/s/c/p letter of the text is used instead,
 * so "%u ms" reads the argument as a string pointer and crashes the C906.
 * The format attribute below does not catch this. Use %d or %x. */
#define PLAYGROUND_PRINT_MAX 160
void pg_print(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

/* Ask the runtime to deliver every IP_SYSTEM command with this cmd_id to
 * the returned queue (items are cmdqu_t). At most PG_MAX_SUBSCRIPTIONS ids.
 * Vendor ids (>= 0x50) cannot be subscribed. Returns NULL on error.
 * Call from playground_app_init(). Unsubscribed playground ids are echoed
 * back unchanged, as the vendor comm task does for unknown commands. */
#define PG_MAX_SUBSCRIPTIONS 4
QueueHandle_t pg_cmd_subscribe(uint8_t cmd_id);

/* Reply to a command received from a subscription queue: same ip_id and
 * cmd_id, block bit kept, param_ptr replaced. Returns 0 on success. */
int pg_cmd_reply(const cmdqu_t *request, uint32_t param);

#endif /* PLAYGROUND_H */
