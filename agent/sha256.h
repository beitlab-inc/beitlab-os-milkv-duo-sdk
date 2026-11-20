/* SPDX-License-Identifier: MIT */
#ifndef RTOS_AGENT_SHA256_H
#define RTOS_AGENT_SHA256_H

#include <stddef.h>
#include <stdint.h>

struct sha256 {
	uint32_t h[8];
	uint64_t len;
	uint8_t buf[64];
	size_t fill;
};

void sha256_init(struct sha256 *s);
void sha256_update(struct sha256 *s, const void *data, size_t n);
void sha256_final(struct sha256 *s, uint8_t out[32]);
/* hex must hold 65 bytes */
void sha256_hex(const void *data, size_t n, char hex[65]);

#endif
