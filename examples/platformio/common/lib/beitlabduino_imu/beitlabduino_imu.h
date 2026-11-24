/* SPDX-License-Identifier: MIT */
#ifndef BEITLABDUINO_IMU_H
#define BEITLABDUINO_IMU_H
#include <stdint.h>

/* Optional beitlabduino module, task context (setup/loop), one caller.
 * custom_libs = beitlabduino beitlabduino_imu
 * The profile's I2C4 route selects the sensor, polling only, no INT1,
 * GPIO output or motors:
 *   beitlab_robotics_rev011_imu  carrier BMI323, I2C4 B1/B2, 0x68
 *   duos_bare_bmi160_j3          standalone Duo S BMI160, J3 B20/B21, 0x68/0x69
 * Other profiles are refused before any bus access. */
typedef struct {
	int32_t accel_mg[3];       /* sensor X/Y/Z, 1000 mg = 1 g */
	int32_t gyro_mdps[3];     /* sensor X/Y/Z, 1000 mdps = 1 degree/second */
	int32_t temperature_mc;  /* 1000 mC = 1 degree Celsius */
} bl_imu_sample_t;

enum { BL_IMU_ERROR = -1, BL_IMU_NOT_READY = 0, BL_IMU_SAMPLE_READY = 1 };

/* Initialize or reinitialize: 0 on success, -1 on failure with a diagnostic
 * printed through bl_print. Waits at least 100 ms for the board/sensor.
 * Configures 100 Hz, +/-4 g, +/-2000 deg/s. Does not start another task. */
int bl_imu_begin(void);

/* 1: fresh sample; 0: wait for data; -1: error (call begin again).
 * Output stays unchanged unless the result is BL_IMU_SAMPLE_READY.
 * Sensor and transport failures print a diagnostic. NULL output is an error. */
int bl_imu_read(bl_imu_sample_t *sample);

/* Sensor and connection, e.g. "BMI323 carrier I2C4 0x68", or NULL until
 * bl_imu_begin() succeeds. */
const char *bl_imu_sensor(void);
#endif
