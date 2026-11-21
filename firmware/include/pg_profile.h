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

/* Check before touching controller registers/muxes. NULL means available;
 * I2C drivers must also check the individual address below. */
const char *pg_profile_resource(const char *name);

/* Seven-bit I2C address authorization. NULL means allowed. No hardware access.
 * A device grant never grants general GPIO output on the bus/route pads. */
const char *pg_profile_i2c_address(unsigned bus, unsigned address);

enum pg_i2c4_route {
	PG_I2C4_NONE = 0, PG_I2C4_CARRIER = 1, PG_I2C4_J3 = 2
};
/* Verified controller routing; no MMIO. J3 is only for a standalone Duo S. */
enum pg_i2c4_route pg_profile_i2c4_route(void);

#endif
