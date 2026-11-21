/* SPDX-License-Identifier: MIT */
#ifndef EXAMPLE_BMI323_H
#define EXAMPLE_BMI323_H
#include <stddef.h>
#include <stdint.h>

/* Bus reads return the wire bytes, including the BMI323's two dummy bytes. */
struct bmi323_io {
	void *context;
	int (*read)(void *, uint8_t, uint8_t *, size_t);
	int (*write)(void *, uint8_t, const uint8_t *, size_t);
	void (*delay_ms)(void *, unsigned);
};
struct bmi323 {
	struct bmi323_io io;
	uint16_t chip_id, errors;
	int ready;
};
struct bmi323_sample {
	int16_t accel[3], gyro[3], temperature;
};
enum {
	BMI323_OK = 0, BMI323_NOT_READY = 1,
	BMI323_BUS_ERROR = -1, BMI323_WRONG_ID = -2,
	BMI323_CONFIG_ERROR = -3, BMI323_SENSOR_ERROR = -4
};
int bmi323_start(struct bmi323 *imu);
int bmi323_read_sample(struct bmi323 *imu, struct bmi323_sample *sample);
int32_t bmi323_accel_mg(int16_t raw);
int32_t bmi323_gyro_mdps(int16_t raw);
int32_t bmi323_temperature_mc(int16_t raw);
#endif
