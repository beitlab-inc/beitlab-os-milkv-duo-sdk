/* SPDX-License-Identifier: MIT */
#include <stddef.h>
#include "beitlabduino.h"
#include "beitlabduino_bmi160.h"
#include "bmi160.h"
#include "i2c4.h"

static void sensor_delay(void *unused, unsigned ms)
{
	(void)unused;
	/* A FreeRTOS delay can lose the partial current tick. Add one full
	 * tick so BMI160's minimum post-write/startup delays are respected. */
	bl_delay(ms + (1000u + configTICK_RATE_HZ - 1) / configTICK_RATE_HZ);
}
/* bmi160.c folds transport failures into BMI160_BUS_ERROR; keep the first
 * I2C4 code and register of the current operation for the diagnostic. */
static int bus_error;
static unsigned bus_reg;

static void note_bus(int rc, uint8_t reg)
{
	if (rc && !bus_error) {
		bus_error = rc;
		bus_reg = reg;
	}
}

static int bus_read(void *context, uint8_t reg, uint8_t *data, size_t length)
{
	int rc = imu_i2c4_read(context, reg, data, length);

	note_bus(rc, reg);
	return rc;
}

static int bus_write(void *context, uint8_t reg, const uint8_t *data, size_t length)
{
	int rc = imu_i2c4_write(context, reg, data, length);

	note_bus(rc, reg);
	return rc;
}

static void print_fault(void)
{
	struct imu_i2c4_fault f;

	if (!bus_error)
		return;
	f = imu_i2c4_last_fault();
	bl_print("BMI160 I2C4 fault stage=%d byte=%d status=%x raw_intr=%x txflr=%d rxflr=%d en=%x",
		 (int)f.stage, (int)f.index, (unsigned)f.status, (unsigned)f.raw_intr,
		 (int)f.txflr, (int)f.rxflr, (unsigned)f.enable_status);
}

static struct bmi160 imu = {.io = {NULL, bus_read, bus_write, sensor_delay}};
static unsigned address;

int bl_bmi160_begin(void)
{
	unsigned candidate;
	uint8_t chip = 0;
	int rc;

	imu.ready = 0;
	address = 0;
	if (pg_profile_i2c4_route() != PG_I2C4_J3) {
		bl_print("BMI160 refused: select duos_bare_bmi160_j3; disconnect robotics carrier");
		return BL_BMI160_ERROR;
	}
	bl_delay(100);
	for (candidate = 0x68; candidate <= 0x69; candidate++) {
		rc = imu_i2c4_init_at(candidate);
		if (rc) {
			struct imu_i2c4_init_state state = imu_i2c4_last_init_state();
			bl_print("BMI160 I2C4 init error=%d en=%x state=%x status=%x; sensor not probed", rc,
				 (unsigned)state.enable, (unsigned)state.enable_status, (unsigned)state.status);
			return BL_BMI160_ERROR;
		}
		if (imu_i2c4_read(NULL, 0x00, &chip, 1) || chip != 0xd1)
			continue;
		bus_error = 0;
		rc = bmi160_start(&imu);
		if (rc) {
			bl_print("BMI160 init error=%d i2c=%d reg=%x chip=%x err=%x abort=%x", rc,
				 bus_error, bus_error ? bus_reg : 0u, (unsigned)imu.chip_id,
				 (unsigned)imu.errors, (unsigned)imu_i2c4_abort_source());
			print_fault();
			return BL_BMI160_ERROR;
		}
		address = candidate;
		return 0;
	}
	bl_print("BMI160 not found at 0x68/0x69 (last chip=%x abort=%x); check J3 wiring, CS and SDO",
		 (unsigned)chip, (unsigned)imu_i2c4_abort_source());
	return BL_BMI160_ERROR;
}

int bl_bmi160_read(bl_bmi160_sample_t *sample)
{
	struct bmi160_sample raw;
	bl_bmi160_sample_t converted;
	unsigned i;
	int rc;

	if (!sample || !imu.ready)
		return BL_BMI160_ERROR;
	bus_error = 0;
	rc = bmi160_read_sample(&imu, &raw);
	if (rc == BMI160_NOT_READY)
		return BL_BMI160_NOT_READY;
	if (rc) {
		imu.ready = 0;
		bl_print("BMI160 data error=%d i2c=%d reg=%x err=%x abort=%x; reinitialize", rc,
			 bus_error, bus_error ? bus_reg : 0u, (unsigned)imu.errors,
			 (unsigned)imu_i2c4_abort_source());
		print_fault();
		return BL_BMI160_ERROR;
	}
	for (i = 0; i < 3; i++) {
		converted.accel_mg[i] = bmi160_accel_mg(raw.accel[i]);
		converted.gyro_mdps[i] = bmi160_gyro_mdps(raw.gyro[i]);
	}
	converted.temperature_mc = bmi160_temperature_mc(raw.temperature);
	*sample = converted;
	return BL_BMI160_SAMPLE_READY;
}

unsigned bl_bmi160_address(void) { return imu.ready ? address : 0; }
