/* SPDX-License-Identifier: MIT */
/* Independent polling implementation from Bosch BST-BMI160-DS000-09.
 * Byte registers, no I2C dummy bytes; unlike the carrier's BMI323. */
#include "bmi160.h"

#define CHIP_ID 0x00
#define ERR_REG 0x02
#define PMU_STATUS 0x03
#define GYR_DATA 0x0c
#define STATUS 0x1b
#define TEMPERATURE 0x20
#define ACC_CONF 0x40
#define INT_EN 0x50
#define CMD 0x7e
#define ERRORS 0x5f /* fatal/error_code/dropped command; ignore reserved bits */

static int read_bytes(struct bmi160 *imu, uint8_t reg, uint8_t *bytes, size_t length)
{
	return imu->io.read(imu->io.context, reg, bytes, length) ? BMI160_BUS_ERROR : 0;
}

static int write_bytes(struct bmi160 *imu, uint8_t reg, const uint8_t *bytes, size_t length)
{
	size_t i;
	/* Burst writes are unsupported when all sensors are suspended. Use
	 * individual registers and >=450 us post-write idle in every mode. */
	for (i = 0; i < length; i++) {
		if (imu->io.write(imu->io.context, (uint8_t)(reg + i), bytes + i, 1))
			return BMI160_BUS_ERROR;
		imu->io.delay_ms(imu->io.context, 1);
	}
	return 0;
}

static int command(struct bmi160 *imu, uint8_t cmd, unsigned wait_ms)
{
	if (write_bytes(imu, CMD, &cmd, 1))
		return BMI160_BUS_ERROR;
	imu->io.delay_ms(imu->io.context, wait_ms);
	return 0;
}

static int16_t signed_word(const uint8_t *bytes)
{
	return (int16_t)((uint16_t)bytes[0] | (uint16_t)bytes[1] << 8);
}

int bmi160_start(struct bmi160 *imu)
{
	/* 100 Hz normal filtering, +/-4 g and +/-2000 degrees/second. */
	static const uint8_t settings[] = {0x28, 0x05, 0x28, 0x00};
	static const uint8_t masks[] = {0xff, 0x0f, 0x3f, 0x07};
	static const uint8_t interrupts_off[] = {0, 0, 0, 0};
	uint8_t actual[4], pmu;
	unsigned i;

	if (!imu)
		return BMI160_CONFIG_ERROR;
	imu->ready = 0;
	imu->chip_id = imu->errors = 0;
	if (!imu->io.read || !imu->io.write || !imu->io.delay_ms)
		return BMI160_CONFIG_ERROR;
	if (read_bytes(imu, CHIP_ID, &imu->chip_id, 1))
		return BMI160_BUS_ERROR;
	if (imu->chip_id != 0xd1)
		return BMI160_WRONG_ID; /* No sensor writes before identification. */
	if (command(imu, 0xb6, 10) || read_bytes(imu, CHIP_ID, &imu->chip_id, 1))
		return BMI160_BUS_ERROR;
	if (imu->chip_id != 0xd1)
		return BMI160_WRONG_ID;
	if (write_bytes(imu, ACC_CONF, settings, sizeof(settings)) ||
	    write_bytes(imu, INT_EN, interrupts_off, sizeof(interrupts_off)) ||
	    command(imu, 0x11, 5) || command(imu, 0x15, 100))
		return BMI160_BUS_ERROR;
	if (read_bytes(imu, ACC_CONF, actual, sizeof(actual)) ||
	    read_bytes(imu, ERR_REG, &imu->errors, 1) ||
	    read_bytes(imu, PMU_STATUS, &pmu, 1))
		return BMI160_BUS_ERROR;
	if ((imu->errors & ERRORS) || (pmu & 0x3c) != 0x14)
		return BMI160_SENSOR_ERROR;
	for (i = 0; i < sizeof(settings); i++)
		if ((actual[i] & masks[i]) != settings[i])
			return BMI160_CONFIG_ERROR;
	imu->ready = 1;
	return 0;
}

int bmi160_read_sample(struct bmi160 *imu, struct bmi160_sample *sample)
{
	uint8_t status, pmu, data[12], temp[2];
	struct bmi160_sample result;
	unsigned i;

	if (!imu || !sample || !imu->ready)
		return BMI160_CONFIG_ERROR;
	if (read_bytes(imu, ERR_REG, &imu->errors, 1) ||
	    read_bytes(imu, PMU_STATUS, &pmu, 1) || read_bytes(imu, STATUS, &status, 1))
		return BMI160_BUS_ERROR;
	if ((imu->errors & ERRORS) || (pmu & 0x3c) != 0x14) {
		imu->ready = 0;
		return BMI160_SENSOR_ERROR;
	}
	if ((status & 0xc0) != 0xc0)
		return BMI160_NOT_READY;
	if (read_bytes(imu, GYR_DATA, data, sizeof(data)) ||
	    read_bytes(imu, TEMPERATURE, temp, sizeof(temp)))
		return BMI160_BUS_ERROR;
	result.temperature = signed_word(temp);
	if (result.temperature == INT16_MIN)
		return BMI160_NOT_READY;
	for (i = 0; i < 3; i++) {
		result.gyro[i] = signed_word(data + i * 2);
		result.accel[i] = signed_word(data + 6 + i * 2);
	}
	*sample = result;
	return 0;
}

int32_t bmi160_accel_mg(int16_t raw) { return (int32_t)raw * 1000 / 8192; }
int32_t bmi160_gyro_mdps(int16_t raw) { return (int32_t)((int64_t)raw * 2000000 / 32768); }
int32_t bmi160_temperature_mc(int16_t raw) { return 23000 + (int32_t)raw * 1000 / 512; }
