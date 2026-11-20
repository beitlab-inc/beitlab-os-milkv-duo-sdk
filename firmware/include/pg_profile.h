/* SPDX-License-Identifier: MIT */
#ifndef PG_PROFILE_H
#define PG_PROFILE_H

struct pg_pin_id {
	unsigned port; /* A=0 ... E=4; E is recognized but not driven by this runtime. */
	unsigned bit;
};

const char *pg_profile_name(void);

/* No register access. NULL means allowed; otherwise returns the refusal reason.
 * Parses aliases such as B019 into the same identity as B19. On success, fills
 * pin if non-NULL. Unknown pins/functions are denied. */
const char *pg_profile_gpio_output(const char *pad, struct pg_pin_id *pin);

/* Future peripheral drivers must check this before touching registers/muxes.
 * This version has no application peripheral grants: every resource is denied,
 * with a specific explanation for reserved controllers. NULL would mean allowed. */
const char *pg_profile_resource(const char *name);

#endif
