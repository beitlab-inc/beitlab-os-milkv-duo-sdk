/* SPDX-License-Identifier: MIT */
/* Standalone Duo S + BMI160 on 3.3 V J3, using setup()/loop(). */
#include "beitlabduino.h"
#include "beitlabduino_bmi160.h"

static int sensor_ready;
static unsigned missed;

static int start_sensor(void)
{
	if (bl_bmi160_begin())
		return 0;
	missed = 0;
	bl_print("BMI160 ready at %x: J3-3 B20=SCL, J3-5 B21=SDA; 100Hz, print 10Hz",
		 bl_bmi160_address());
	bl_print("Units: a_mg=0.001g, g_mdps=0.001deg/s, temp_mC=0.001C");
	return 1;
}

void setup(void)
{
	bl_print("standalone BMI160 profile %s", pg_profile_name());
	sensor_ready = start_sensor();
}

void loop(void)
{
	bl_bmi160_sample_t sample;
	int rc;

	if (!sensor_ready) {
		bl_delay(1000);
		sensor_ready = start_sensor();
		return;
	}
	rc = bl_bmi160_read(&sample);
	if (rc == BL_BMI160_SAMPLE_READY) {
		missed = 0;
		bl_print("bmi160 a_mg=%d,%d,%d g_mdps=%d,%d,%d temp_mC=%d",
			 (int)sample.accel_mg[0], (int)sample.accel_mg[1], (int)sample.accel_mg[2],
			 (int)sample.gyro_mdps[0], (int)sample.gyro_mdps[1], (int)sample.gyro_mdps[2],
			 (int)sample.temperature_mc);
	} else if (rc == BL_BMI160_ERROR || ++missed >= 10) {
		if (rc == BL_BMI160_NOT_READY)
			bl_print("BMI160: no fresh data for 1 s; retrying");
		sensor_ready = 0;
	}
	bl_delay(100);
}
