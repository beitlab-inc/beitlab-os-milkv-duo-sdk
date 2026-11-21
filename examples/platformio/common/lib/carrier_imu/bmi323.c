/* SPDX-License-Identifier: MIT */
/* Register protocol from Bosch BST-BMI323-DS000-13, sections 4, 6 and 7.
 * Independent minimal polling implementation; no robot firmware is linked. */
#include "bmi323.h"

#define CHIP_ID       0x00
#define ERR_REG       0x01
#define STATUS        0x02
#define ACC_DATA_X    0x03
#define ACC_CONF      0x20
#define GYR_CONF      0x21
#define CMD           0x7e
#define EXPECTED_ID   0x43
#define SENSOR_ERRORS 0x0061 /* fatal, accelerometer/gyro configuration errors */
#define DATA_READY    0x00c0
/* High-performance, 100 Hz, ODR/4 bandwidth, +/-4 g and +/-2000 deg/s. */
#define ACC_SETTINGS  0x7098
#define GYR_SETTINGS  0x70c8

static int read_words(struct bmi323 *imu, uint8_t reg, uint16_t *words, size_t count)
{
	uint8_t wire[16];
	size_t i;

	if (!count || count > 7 || imu->io.read(imu->io.context, reg, wire, 2 + count * 2))
		return BMI323_BUS_ERROR;
	for (i = 0; i < count; i++)
		words[i] = (uint16_t)wire[2 + i * 2] | ((uint16_t)wire[3 + i * 2] << 8);
	return BMI323_OK;
}

static int write_word(struct bmi323 *imu, uint8_t reg, uint16_t value)
{
	uint8_t bytes[2] = {(uint8_t)value, (uint8_t)(value >> 8)};

	return imu->io.write(imu->io.context, reg, bytes, sizeof(bytes)) ? BMI323_BUS_ERROR : BMI323_OK;
}

int bmi323_start(struct bmi323 *imu)
{
	uint16_t status, settings[2];

	if (!imu)
		return BMI323_CONFIG_ERROR;
	imu->ready = 0;
	imu->chip_id = imu->errors = 0;
	if (!imu->io.read || !imu->io.write || !imu->io.delay_ms)
		return BMI323_CONFIG_ERROR;
	if (read_words(imu, CHIP_ID, &imu->chip_id, 1))
		return BMI323_BUS_ERROR;
	/* Do not write to a different part, including older BMI160 carriers. */
	if ((imu->chip_id & 0xff) != EXPECTED_ID)
		return BMI323_WRONG_ID;
	if (write_word(imu, CMD, 0xdeaf))
		return BMI323_BUS_ERROR;
	imu->io.delay_ms(imu->io.context, 10);
	if (read_words(imu, STATUS, &status, 1) || read_words(imu, ERR_REG, &imu->errors, 1))
		return BMI323_BUS_ERROR;
	if (!(status & 1) || (imu->errors & SENSOR_ERRORS))
		return BMI323_SENSOR_ERROR;
	/* Reset leaves both physical interrupt outputs disabled. No INT1 GPIO. */
	if (write_word(imu, ACC_CONF, ACC_SETTINGS) || write_word(imu, GYR_CONF, GYR_SETTINGS))
		return BMI323_BUS_ERROR;
	imu->io.delay_ms(imu->io.context, 50);
	if (read_words(imu, ACC_CONF, settings, 2) || read_words(imu, ERR_REG, &imu->errors, 1))
		return BMI323_BUS_ERROR;
	if (imu->errors & SENSOR_ERRORS)
		return BMI323_SENSOR_ERROR;
	if (settings[0] != ACC_SETTINGS || settings[1] != GYR_SETTINGS)
		return BMI323_CONFIG_ERROR;
	imu->ready = 1;
	return BMI323_OK;
}

int bmi323_read_sample(struct bmi323 *imu, struct bmi323_sample *sample)
{
	uint16_t status, words[7];
	struct bmi323_sample result;
	unsigned i;

	if (!imu || !sample || !imu->ready)
		return BMI323_CONFIG_ERROR;
	if (read_words(imu, ERR_REG, &imu->errors, 1) || read_words(imu, STATUS, &status, 1))
		return BMI323_BUS_ERROR;
	if ((imu->errors & SENSOR_ERRORS) || (status & 1)) {
		imu->ready = 0; /* Sensor reset/brownout: do not decode with stale ranges. */
		return BMI323_SENSOR_ERROR;
	}
	if ((status & DATA_READY) != DATA_READY)
		return BMI323_NOT_READY;
	if (read_words(imu, ACC_DATA_X, words, 7))
		return BMI323_BUS_ERROR;
	for (i = 0; i < 7; i++)
		if (words[i] == 0x8000) /* Sensor's invalid-data sentinel. */
			return BMI323_NOT_READY;
	for (i = 0; i < 3; i++) {
		result.accel[i] = (int16_t)words[i];
		result.gyro[i] = (int16_t)words[3 + i];
	}
	result.temperature = (int16_t)words[6];
	*sample = result;
	return BMI323_OK;
}

int32_t bmi323_accel_mg(int16_t raw)
{
	return (int32_t)raw * 1000 / 8192;
}

int32_t bmi323_gyro_mdps(int16_t raw)
{
	return (int32_t)((int64_t)raw * 2000000 / 32768);
}

int32_t bmi323_temperature_mc(int16_t raw)
{
	return 23000 + (int32_t)raw * 1000 / 512;
}
