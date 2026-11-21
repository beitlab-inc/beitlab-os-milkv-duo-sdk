/* SPDX-License-Identifier: MIT */
#ifndef BEITLABDUINO_BMI160_H
#define BEITLABDUINO_BMI160_H
#include <stdint.h>
/* Standalone Duo S, J3 B20/SCL and B21/SDA, 3.3 V. No robotics carrier.
 * custom_libs = beitlabduino duos_i2c4 beitlabduino_bmi160
 * custom_profile = duos_bare_bmi160_j3. Call from setup/loop, one caller. */
typedef struct {
	int32_t accel_mg[3], gyro_mdps[3], temperature_mc;
} bl_bmi160_sample_t;
enum { BL_BMI160_ERROR = -1, BL_BMI160_NOT_READY = 0, BL_BMI160_SAMPLE_READY = 1 };
/* Read-only identity probes at 0x68/0x69; configure only a chip with ID 0xD1.
 * Returns 0 on success, -1 with a diagnostic otherwise. May be retried. */
int bl_bmi160_begin(void);
/* Returns 1/0/-1 as above. Output changes only on SAMPLE_READY.
 * Faults require begin() again. Integer units: mg, mdps, milli-degrees C. */
int bl_bmi160_read(bl_bmi160_sample_t *sample);
/* Selected seven-bit address, or zero until initialization succeeds. */
unsigned bl_bmi160_address(void);
#endif
