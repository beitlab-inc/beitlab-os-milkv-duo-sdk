/* SPDX-License-Identifier: GPL-2.0 */
/*
 * Vendor userspace ABI used by rtos-agent, copied from the SDK at the
 * sdk.lock commit (6f8962c394dd0a05729abb089f0feb7d5cc4aa5e). Do not edit
 * by hand; if sdk.lock moves, re-copy and re-check docs/sdk-notes.md.
 *
 *   osdrv/interdrv/rtos_cmdqu/rtos_cmdqu.h      enum SYSTEM_CMD_TYPE
 *   osdrv/interdrv/fast_image/fast_image.h      enum FAST_IMAGE_CMD_TYPE,
 *                                               FAST_IMAGE_QUERY_DUMP_MSG*
 *   osdrv/interdrv/fast_image/dump_uart.h       struct dump_uart_s
 *
 * Copyright (C) Cvitek Co., Ltd. 2019-2022. All rights reserved.
 */
#ifndef RTOS_AGENT_VENDOR_ABI_H
#define RTOS_AGENT_VENDOR_ABI_H

#include <stdint.h>
#include <sys/ioctl.h>

/* ---- rtos_cmdqu.h (excerpt) ---- */
#define NR_SYSTEM_CMD           20
enum SYSTEM_CMD_TYPE {
	CMDQU_SEND = 1,
	CMDQU_REQUEST,
	CMDQU_REQUEST_FREE,
	CMDQU_SEND_WAIT,
	CMDQU_SEND_WAKEUP,
	CMDQU_SYSTEM_LIMIT = NR_SYSTEM_CMD,
};

/* ---- fast_image.h (excerpt) ---- */
enum FAST_IMAGE_CMD_TYPE {
	FAST_SEND_STOP_REC = CMDQU_SYSTEM_LIMIT,
	FAST_SEND_QUERY_ISP_PADDR,
	FAST_SEND_QUERY_ISP_VADDR,
	FAST_SEND_QUERY_ISP_SIZE,
	FAST_SEND_QUERY_ISP_CTXT,
	FAST_SEND_QUERY_IMG_PADDR,
	FAST_SEND_QUERY_IMG_VADDR,
	FAST_SEND_QUERY_IMG_SIZE,
	FAST_SEND_QUERY_IMG_CTXT,
	FAST_SEND_QUERY_ENC_PADDR,
	FAST_SEND_QUERY_ENC_VADDR,
	FAST_SEND_QUERY_ENC_SIZE,
	FAST_SEND_QUERY_ENC_CTXT,
	FAST_SEND_QUERY_FREE_ISP_ION,
	FAST_SEND_QUERY_FREE_IMG_ION,
	FAST_SEND_QUERY_FREE_ENC_ION,
	FAST_SEND_QUERY_DUMP_MSG,
	FAST_SEND_QUERY_DUMP_MSG_INFO,
	FAST_SEND_QUERY_DUMP_EN,
	FAST_SEND_QUERY_DUMP_DIS,
	FAST_SEND_QUERY_DUMP_JPG,
	FAST_SEND_QUERY_DUMP_JPG_INFO,
	FAST_SEND_QUERY_TRACE_SNAPSHOT_START,
	FAST_SEND_QUERY_TRACE_SNAPSHOT_STOP,
	FAST_SEND_QUERY_TRACE_SNAPSHOT_DUMP,
	FAST_SEND_QUERY_TRACE_STREAM_START,
	FAST_SEND_QUERY_TRACE_STREAM_STOP,
	FAST_SEND_QUERY_TRACE_STREAM_DUMP,
	FAST_SEND_LIMIT,
};

#define FAST_IMAGE_DEV_NAME "cvi-fast-image"
#define FAST_IMAGE_QUERY_DUMP_MSG               _IOW('r', FAST_SEND_QUERY_DUMP_MSG, unsigned long)
#define FAST_IMAGE_QUERY_DUMP_MSG_INFO          _IOW('r', FAST_SEND_QUERY_DUMP_MSG_INFO, unsigned long)

/* ---- dump_uart.h ---- */
#define DUMP_PRINT_DEFAULT_SIZE 0x1000

/* this structure should be modified both freertos & osdrv side */
struct dump_uart_s {
	uint64_t dump_uart_ptr;
	uint32_t  dump_uart_max_size;
	uint32_t  dump_uart_pos;
	uint8_t  dump_uart_enable;
	uint8_t dump_uart_overflow;
} __attribute__((packed));

#endif /* RTOS_AGENT_VENDOR_ABI_H */
