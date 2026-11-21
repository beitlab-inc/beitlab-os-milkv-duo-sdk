/* SPDX-License-Identifier: MIT */
#include <stddef.h>
#include <string.h>
#include "pg_profile.h"

struct profile_pin {
	unsigned port, bit, allowed;
	const char *reason;
};
struct profile_resource {
	const char *name, *reason;
};
struct profile_i2c_device {
	unsigned bus, address;
};
#include "pg_profile_generated.h"

const char *pg_profile_name(void)
{
	return profile_name;
}

const char *pg_profile_gpio_output(const char *pad, struct pg_pin_id *pin)
{
	struct pg_pin_id id;
	const char *p;
	unsigned i, digit;

	if (!pad || pad[0] < 'A' || pad[0] > 'E' || !pad[1])
		return "invalid pad; use a port A-E and bit 0-31";
	id.port = (unsigned)(pad[0] - 'A');
	id.bit = 0;
	for (p = pad + 1; *p; p++) {
		if (*p < '0' || *p > '9')
			return "invalid pad; use a port A-E and bit 0-31";
		digit = (unsigned)(*p - '0');
		/* Check before multiplying, so arbitrarily long input cannot wrap. */
		if (id.bit > 3 || id.bit * 10 + digit > 31)
			return "invalid pad; bit must be 0-31";
		id.bit = id.bit * 10 + digit;
	}
	for (i = 0; profile_pins[i].port != 255; i++) {
		const struct profile_pin *entry = &profile_pins[i];
		if (entry->port != id.port || entry->bit != id.bit)
			continue;
		if (!entry->allowed)
			return entry->reason;
		if (pin)
			*pin = id;
		return NULL;
	}
	return "GPIO output not verified/allowed by this profile";
}

const char *pg_profile_resource(const char *name)
{
	unsigned i;

	if (!name)
		return "invalid resource";
	if (!strcmp(name, "i2c4"))
		for (i = 0; profile_i2c_devices[i].bus != 255; i++)
			if (profile_i2c_devices[i].bus == 4)
				return NULL;
	for (i = 0; profile_resources[i].name; i++)
		if (!strcmp(profile_resources[i].name, name))
			return profile_resources[i].reason;
	return "peripheral access not verified/allowed by this profile";
}

const char *pg_profile_i2c_address(unsigned bus, unsigned address)
{
	unsigned i;

	if (address < 0x08 || address > 0x77)
		return "invalid or reserved seven-bit I2C address";
	for (i = 0; profile_i2c_devices[i].bus != 255; i++)
		if (profile_i2c_devices[i].bus == bus &&
		    profile_i2c_devices[i].address == address)
			return NULL;
	return "I2C device not allowed by this profile";
}

enum pg_i2c4_route pg_profile_i2c4_route(void)
{
	return (enum pg_i2c4_route)profile_i2c4_route;
}
