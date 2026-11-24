/* SPDX-License-Identifier: MIT */
/* Board IMU through the beitlabduino setup()/loop() API: the robotics_board
 * environment reads the carrier BMI323, milkv an external BMI160 on J3. */
#include "beitlabduino.h"
#include "beitlabduino_imu.h"

static int imu_ready;
static unsigned missed;

static int start_imu(void)
{
	if (bl_imu_begin() != 0)
		return 0;
	missed = 0;
	bl_print("beitlabduino IMU ready: %s, print 10Hz", bl_imu_sensor());
	bl_print("Units: a_mg=0.001g, g_mdps=0.001deg/s, temp_mC=0.001C");
	return 1;
}

void setup(void)
{
	bl_print("beitlabduino IMU profile %s", pg_profile_name());
	imu_ready = start_imu();
}

void loop(void)
{
	bl_imu_sample_t sample;
	int rc;

	if (!imu_ready) {
		bl_delay(1000);
		imu_ready = start_imu();
		return;
	}
	rc = bl_imu_read(&sample);
	if (rc == BL_IMU_SAMPLE_READY) {
		missed = 0;
		bl_print("imu a_mg=%d,%d,%d g_mdps=%d,%d,%d temp_mC=%d",
			 (int)sample.accel_mg[0], (int)sample.accel_mg[1],
			 (int)sample.accel_mg[2], (int)sample.gyro_mdps[0],
			 (int)sample.gyro_mdps[1], (int)sample.gyro_mdps[2],
			 (int)sample.temperature_mc);
	} else if (rc == BL_IMU_ERROR || ++missed >= 10) {
		if (rc == BL_IMU_NOT_READY)
			bl_print("beitlabduino IMU: no fresh sample for 1 s; retrying");
		imu_ready = 0;
	}
	bl_delay(100);
}
