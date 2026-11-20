/* SPDX-License-Identifier: MIT */
#ifndef RTOS_AGENT_ELFCHECK_H
#define RTOS_AGENT_ELFCHECK_H

#include <stddef.h>
#include <stdint.h>

/* What a C906L program must look like for the pinned remoteproc driver
 * (docs/sdk-notes.md, "Firmware ABI"). */
struct elf_rules {
	uint64_t boot_addr;   /* C906L reset vector FSBL programs */
	uint64_t region_base; /* rtos_dram carveout */
	uint64_t region_size;
};

/* Returns 0 if the image is acceptable. Otherwise returns -1 and writes a
 * one-line reason into why. */
int elf_check(const uint8_t *data, size_t size, const struct elf_rules *rules,
	      char *why, size_t why_len);

#endif
