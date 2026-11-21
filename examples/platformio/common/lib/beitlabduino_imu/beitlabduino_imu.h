/* SPDX-License-Identifier: MIT */
#ifndef BEITLABDUINO_IMU_H
#define BEITLABDUINO_IMU_H
#include <stdint.h>

/* Optional beitlabduino module, task context (setup/loop), one caller.
 * custom_libs = beitlabduino carrier_imu beitlabduino_imu
 * custom_profile = beitlab_robotics_rev011_imu
 * Carrier BMI323 only: I2C4 0x68, polling, no INT1, GPIO output or motors. */
typedef struct {
	int32_t accel_mg[3];       /* sensor X/Y/Z, 1000 mg = 1 g */
	int32_t gyro_mdps[3];     /* sensor X/Y/Z, 1000 mdps = 1 degree/second */
	int32_t temperature_mc;  /* 1000 mC = 1 degree Celsius */
} bl_imu_sample_t;

enum { BL_IMU_ERROR = -1, BL_IMU_NOT_READY = 0, BL_IMU_SAMPLE_READY = 1 };

/* Initialize or reinitialize: 0 on success, -1 on failure with a diagnostic
 * printed through bl_print. Waits about 160 ms for the carrier/sensor.
 * Configures 100 Hz, +/-4 g, +/-2000 deg/s. Does not start another task. */
int bl_imu_begin(void);

/* 1: fresh sample; 0: wait for data; -1: error (call begin again).
 * Output stays unchanged unless the result is BL_IMU_SAMPLE_READY.
 * Sensor and transport failures print a diagnostic. NULL output is an error. */
int bl_imu_read(bl_imu_sample_t *sample);
#endif
