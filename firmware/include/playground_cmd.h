/* SPDX-License-Identifier: MIT */
/*
 * beitlab Robotics MilkV duo OS mailbox contract.
 *
 * Shared by the starter firmware, every PlatformIO example and host_ping.
 * Transport is the vendor command queue: Linux /dev/cvi-rtos-cmdqu
 * (osdrv/interdrv/rtos_cmdqu) <-> C906L mailbox (freertos/cvitek/driver/
 * rtos_cmdqu). The 8-byte message is cmdqu_t from
 * freertos/cvitek/driver/rtos_cmdqu/include/rtos_cmdqu.h at the sdk.lock
 * commit; this header only chooses ids inside it.
 *
 * Ping:
 *   Linux sends   ip_id = PLAYGROUND_IP_ID, cmd_id = PLAYGROUND_CMD_PING,
 *                 param_ptr = nonce, using RTOS_CMDQU_SEND_WAIT.
 *   C906 replies  same ip_id and cmd_id, block bit kept,
 *                 param_ptr = nonce + 1.
 *
 * Why 0x40 on IP_SYSTEM (= 6):
 *   - cmd_id is a 7-bit field, so ids are 0x00..0x7f.
 *   - The vendor IP_SYSTEM ids are enum SYS_CMD_ID, 0x50
 *     (SYS_CMD_INFO_TRANS) through 0x5f (SYS_CMD_INFO_TRACE_STREAM_STOP);
 *     SYS_CMD_INFO_LIMIT is 0x60.
 *   - The Linux driver rejects cmd_id >= SYS_CMD_INFO_LIMIT
 *     (rtos_cmdqu_send(): "invalid-id"), so an id above 0x5f is unusable.
 *   - 0x40 is below every vendor id and is not used by any IP_SYSTEM
 *     handler in freertos/cvitek/task/comm/src/riscv64/comm_main.c.
 *   The C906 side routes by ip_id (prvQueueISR), so IP_SYSTEM reaches the
 *   command task that the playground runtime owns.
 */
#ifndef PLAYGROUND_CMD_H
#define PLAYGROUND_CMD_H

/* enum IP_TYPE in rtos_cmdqu.h: IP_ISP=0 ... IP_SYSTEM=6, IP_CAMERA=7. */
#define PLAYGROUND_IP_ID           6

#define PLAYGROUND_CMD_PING        0x40

/* Vendor IP_SYSTEM range, for the compile-time checks below. */
#define PLAYGROUND_VENDOR_SYS_FIRST 0x50 /* SYS_CMD_INFO_TRANS */
#define PLAYGROUND_VENDOR_SYS_LIMIT 0x60 /* SYS_CMD_INFO_LIMIT */

#if PLAYGROUND_CMD_PING >= PLAYGROUND_VENDOR_SYS_FIRST
#error "PLAYGROUND_CMD_PING collides with the vendor SYS_CMD_ID range"
#endif
#if PLAYGROUND_CMD_PING > 0x7f
#error "cmd_id is a 7-bit field"
#endif

/* The C906 sets this on every reply (rtos_valid); Linux sets block=1 on
 * SEND_WAIT and only wakes a waiter whose reply keeps block=1. */
#define PLAYGROUND_PING_TIMEOUT_MS 1000

#endif /* PLAYGROUND_CMD_H */
