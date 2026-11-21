/* SPDX-License-Identifier: MIT */
#ifndef EXAMPLE_I2C4_H
#define EXAMPLE_I2C4_H
#include <stddef.h>
#include <stdint.h>
/* Only the carrier BMI323 at seven-bit address 0x68. Task context, one caller.
 * Every polling loop is bounded. No IRQ, DMA, bus scanning or GPIO recovery. */
enum { IMU_I2C_DENIED = -1, IMU_I2C_TIMEOUT = -2, IMU_I2C_ABORT = -3,
       IMU_I2C_CONTROLLER = -4, IMU_I2C_ARGUMENT = -5 };
int imu_i2c4_init(void);
int imu_i2c4_read(void *, uint8_t reg, uint8_t *data, size_t length);
int imu_i2c4_write(void *, uint8_t reg, const uint8_t *data, size_t length);
uint32_t imu_i2c4_abort_source(void);
#endif
