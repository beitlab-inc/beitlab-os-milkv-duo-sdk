/* SPDX-License-Identifier: MIT */
#include <stddef.h>
#include "beitlabduino.h"
#include "beitlabduino_imu.h"
#include "bmi323.h"
#include "i2c4.h"

static void sensor_delay(void *unused, unsigned ms)
{
	(void)unused;
	bl_delay(ms);
}

static struct bmi323 imu = {
	.io = {NULL, imu_i2c4_read, imu_i2c4_write, sensor_delay}
};

int bl_imu_begin(void)
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
	rc = bmi323_start(&imu);
	if (rc) {
		bl_print("beitlabduino IMU init error=%d chip=%x err=%x abort=%x",
			 rc, (unsigned)imu.chip_id, (unsigned)imu.errors,
			 (unsigned)imu_i2c4_abort_source());
		return BL_IMU_ERROR;
	}
	return 0;
}

int bl_imu_read(bl_imu_sample_t *sample)
{
	struct bmi323_sample raw;
	bl_imu_sample_t converted;
	unsigned i;
	int rc;

	if (!sample || !imu.ready)
		return BL_IMU_ERROR;
	rc = bmi323_read_sample(&imu, &raw);
	if (rc == BMI323_NOT_READY)
		return BL_IMU_NOT_READY;
	if (rc) {
		imu.ready = 0;
		bl_print("beitlabduino IMU read error=%d err=%x abort=%x; call bl_imu_begin again",
			 rc, (unsigned)imu.errors, (unsigned)imu_i2c4_abort_source());
		return BL_IMU_ERROR;
	}
	for (i = 0; i < 3; i++) {
		converted.accel_mg[i] = bmi323_accel_mg(raw.accel[i]);
		converted.gyro_mdps[i] = bmi323_gyro_mdps(raw.gyro[i]);
	}
	converted.temperature_mc = bmi323_temperature_mc(raw.temperature);
	*sample = converted;
	return BL_IMU_SAMPLE_READY;
}
