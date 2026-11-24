/* SPDX-License-Identifier: MIT */
#include <stddef.h>
#include "beitlabduino.h"
#include "beitlabduino_bmi160.h"
#include "beitlabduino_imu.h"
#include "bmi323.h"
#include "i2c4.h"
#include "pg_profile.h"

enum { SENSOR_NONE, SENSOR_BMI323, SENSOR_BMI160 };

static void sensor_delay(void *unused, unsigned ms)
{
	(void)unused;
	bl_delay(ms);
}

/* bmi323.c folds every transport failure into BMI323_BUS_ERROR; keep the
 * first I2C4 code of the current operation so diagnostics can name it. */
static int bus_error;

static int bus_read(void *context, uint8_t reg, uint8_t *data, size_t length)
{
	int rc = imu_i2c4_read(context, reg, data, length);

	if (rc && !bus_error)
		bus_error = rc;
	return rc;
}

static int bus_write(void *context, uint8_t reg, const uint8_t *data, size_t length)
{
	int rc = imu_i2c4_write(context, reg, data, length);

	if (rc && !bus_error)
		bus_error = rc;
	return rc;
}

static void print_bus_hint(void)
{
	if (bus_error == IMU_I2C_TIMEOUT && !imu_i2c4_abort_source())
		bl_print("beitlabduino IMU: I2C4 timed out with no NACK, so SCL/SDA are not "
			 "released: check carrier power (PCA9306) or use -e milkv on a bare Duo S");
	else if (bus_error == IMU_I2C_ABORT)
		bl_print("beitlabduino IMU: no ACK at 0x68; check the BMI323 and carrier wiring");
}

static struct bmi323 imu = {
	.io = {NULL, bus_read, bus_write, sensor_delay}
};
static int sensor;

static int begin_bmi323(void)
{
	int rc;

	imu.ready = 0;
	imu.chip_id = imu.errors = 0;
	bl_delay(100);
	rc = imu_i2c4_init();
	if (rc) {
		if (rc == IMU_I2C_DENIED)
			bl_print("beitlabduino IMU refused: select beitlab_robotics_rev011_imu");
		else
			bl_print("beitlabduino IMU bus error=%d abort=%x; check carrier power/wiring",
				 rc, (unsigned)imu_i2c4_abort_source());
		return BL_IMU_ERROR;
	}
	bus_error = 0;
	rc = bmi323_start(&imu);
	if (rc) {
		bl_print("beitlabduino IMU init error=%d i2c=%d chip=%x err=%x abort=%x",
			 rc, bus_error, (unsigned)imu.chip_id, (unsigned)imu.errors,
			 (unsigned)imu_i2c4_abort_source());
		print_bus_hint();
		return BL_IMU_ERROR;
	}
	sensor = SENSOR_BMI323;
	return 0;
}

static int read_bmi323(bl_imu_sample_t *sample)
{
	struct bmi323_sample raw;
	unsigned i;
	int rc;

	if (!imu.ready)
		return BL_IMU_ERROR;
	bus_error = 0;
	rc = bmi323_read_sample(&imu, &raw);
	if (rc == BMI323_NOT_READY)
		return BL_IMU_NOT_READY;
	if (rc) {
		imu.ready = 0;
		bl_print("beitlabduino IMU read error=%d i2c=%d err=%x abort=%x; call bl_imu_begin again",
			 rc, bus_error, (unsigned)imu.errors, (unsigned)imu_i2c4_abort_source());
		print_bus_hint();
		return BL_IMU_ERROR;
	}
	for (i = 0; i < 3; i++) {
		sample->accel_mg[i] = bmi323_accel_mg(raw.accel[i]);
		sample->gyro_mdps[i] = bmi323_gyro_mdps(raw.gyro[i]);
	}
	sample->temperature_mc = bmi323_temperature_mc(raw.temperature);
	return BL_IMU_SAMPLE_READY;
}

static int read_bmi160(bl_imu_sample_t *sample)
{
	bl_bmi160_sample_t raw;
	unsigned i;
	int rc;

	rc = bl_bmi160_read(&raw);
	if (rc != BL_BMI160_SAMPLE_READY)
		return rc == BL_BMI160_NOT_READY ? BL_IMU_NOT_READY : BL_IMU_ERROR;
	for (i = 0; i < 3; i++) {
		sample->accel_mg[i] = raw.accel_mg[i];
		sample->gyro_mdps[i] = raw.gyro_mdps[i];
	}
	sample->temperature_mc = raw.temperature_mc;
	return BL_IMU_SAMPLE_READY;
}

int bl_imu_begin(void)
{
	sensor = SENSOR_NONE;
	switch (pg_profile_i2c4_route()) {
	case PG_I2C4_CARRIER:
		return begin_bmi323();
	case PG_I2C4_J3:
		if (bl_bmi160_begin())
			return BL_IMU_ERROR;
		sensor = SENSOR_BMI160;
		return 0;
	default:
		bl_print("beitlabduino IMU refused: select beitlab_robotics_rev011_imu (carrier) "
			 "or duos_bare_bmi160_j3 (standalone Duo S)");
		return BL_IMU_ERROR;
	}
}

int bl_imu_read(bl_imu_sample_t *sample)
{
	bl_imu_sample_t converted;
	int rc;

	if (!sample)
		return BL_IMU_ERROR;
	if (sensor == SENSOR_BMI323)
		rc = read_bmi323(&converted);
	else if (sensor == SENSOR_BMI160)
		rc = read_bmi160(&converted);
	else
		return BL_IMU_ERROR;
	if (rc == BL_IMU_ERROR)
		sensor = SENSOR_NONE;
	else if (rc == BL_IMU_SAMPLE_READY)
		*sample = converted;
	return rc;
}

const char *bl_imu_sensor(void)
{
	if (sensor == SENSOR_BMI323)
		return "BMI323 carrier I2C4 0x68";
	if (sensor == SENSOR_BMI160)
		return bl_bmi160_address() == 0x69 ? "BMI160 J3 I2C4 0x69" : "BMI160 J3 I2C4 0x68";
	return NULL;
}
