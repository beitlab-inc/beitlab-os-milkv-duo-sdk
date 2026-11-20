/* SPDX-License-Identifier: MIT */
#include <stddef.h>
#include <stdint.h>
#include "FreeRTOS.h"
#include "task.h"
#include "mmio.h"
#include "gpio.h"
#include "playground.h"
#include "pg_gpio.h"

#define GPIO_DR  0x0
#define GPIO_DDR 0x4
#define PG_MAX_PINS 8

static struct {
	struct pg_pin_id id;
	uintptr_t base;
	uint32_t mask;
} pins[PG_MAX_PINS];
static unsigned npins;

static int find_pin(struct pg_pin_id id)
{
	unsigned i;
	for (i = 0; i < npins; i++)
		if (pins[i].id.port == id.port && pins[i].id.bit == id.bit)
			return (int)i;
	return -1;
}

int pg_gpio_output(const char *pad)
{
	static const uintptr_t bases[] = {
		CVI_GPIOA_BASE, CVI_GPIOB_BASE, CVI_GPIOC_BASE, CVI_GPIOD_BASE
	};
	struct pg_pin_id id;
	const char *reason = pg_profile_gpio_output(pad, &id);
	uintptr_t base;
	uint32_t mask;

	if (reason) {
		pg_print("GPIO %s refused [%s]: %s", pad ? pad : "(null)",
			 pg_profile_name(), reason);
		return -1;
	}
	if (id.port >= sizeof(bases) / sizeof(bases[0]))
		return -1;
	base = bases[id.port];
	mask = 1u << id.bit;
	taskENTER_CRITICAL();
	if (find_pin(id) >= 0) {
		taskEXIT_CRITICAL();
		return 0;
	}
	if (npins == PG_MAX_PINS) {
		taskEXIT_CRITICAL();
		pg_print("GPIO refused: at most %d outputs", PG_MAX_PINS);
		return -1;
	}
	/* Change only this bit: Linux owns other pins on the same port.
	 * The critical section serializes C906 tasks, not Linux on the A53. */
	mmio_write_32(base + GPIO_DR, mmio_read_32(base + GPIO_DR) & ~mask);
	mmio_write_32(base + GPIO_DDR, mmio_read_32(base + GPIO_DDR) | mask);
	pins[npins].id = id;
	pins[npins].base = base;
	pins[npins].mask = mask;
	npins++;
	taskEXIT_CRITICAL();
	return 0;
}

int pg_gpio_write(const char *pad, int level)
{
	struct pg_pin_id id;
	uint32_t v;
	int index;

	if (pg_profile_gpio_output(pad, &id))
		return -1;
	taskENTER_CRITICAL();
	index = find_pin(id);
	if (index < 0) {
		taskEXIT_CRITICAL();
		return -1;
	}
	v = mmio_read_32(pins[index].base + GPIO_DR);
	mmio_write_32(pins[index].base + GPIO_DR,
		     level ? (v | pins[index].mask) : (v & ~pins[index].mask));
	taskEXIT_CRITICAL();
	return 0;
}
