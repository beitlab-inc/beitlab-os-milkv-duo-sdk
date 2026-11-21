/* SPDX-License-Identifier: MIT */
#ifndef DUOS_BMI160_H
#define DUOS_BMI160_H
#include <stddef.h>
#include <stdint.h>
struct bmi160_io {
	void *context;
	int (*read)(void *, uint8_t, uint8_t *, size_t);
	int (*write)(void *, uint8_t, const uint8_t *, size_t);
	void (*delay_ms)(void *, unsigned); /* Must wait at least the requested time. */
};
struct bmi160 {
	struct bmi160_io io;
	uint8_t chip_id, errors;
	int ready;
};
struct bmi160_sample { int16_t accel[3], gyro[3], temperature; };
enum { BMI160_OK = 0, BMI160_NOT_READY = 1, BMI160_BUS_ERROR = -1,
       BMI160_WRONG_ID = -2, BMI160_CONFIG_ERROR = -3, BMI160_SENSOR_ERROR = -4 };
int bmi160_start(struct bmi160 *imu);
int bmi160_read_sample(struct bmi160 *imu, struct bmi160_sample *sample);
int32_t bmi160_accel_mg(int16_t raw);
int32_t bmi160_gyro_mdps(int16_t raw);
int32_t bmi160_temperature_mc(int16_t raw);
#endif
