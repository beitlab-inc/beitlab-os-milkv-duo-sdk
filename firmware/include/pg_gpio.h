/* SPDX-License-Identifier: MIT */
#ifndef PG_GPIO_H
#define PG_GPIO_H

#include "pg_profile.h"

/* Task-context GPIO API shared by heartbeat and beitlabduino.
 * Check the profile before any register access. Output starts low.
 * Repeated initialization is idempotent. Write requires initialization.
 * Returns 0 or -1; refused initialization prints the profile and reason.
 * Does not change pinmux. Supports only the explicitly allowed A-D pads. */
int pg_gpio_output(const char *pad);
int pg_gpio_write(const char *pad, int level);

#endif
