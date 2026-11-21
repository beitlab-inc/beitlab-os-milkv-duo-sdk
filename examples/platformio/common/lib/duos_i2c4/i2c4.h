/* SPDX-License-Identifier: MIT */
#ifndef EXAMPLE_I2C4_H
#define EXAMPLE_I2C4_H
#include <stddef.h>
#include <stdint.h>
/* Profile-selected carrier/J3 I2C4 route, authorized seven-bit targets only.
 * Task context, one caller. Bounded polling, no IRQ/DMA or GPIO recovery. */
enum { IMU_I2C_DENIED = -1, IMU_I2C_TIMEOUT = -2, IMU_I2C_ABORT = -3,
       IMU_I2C_CONTROLLER = -4, IMU_I2C_ARGUMENT = -5 };
int imu_i2c4_init(void);
/* The no-argument initializer retains the carrier's default address 0x68. */
int imu_i2c4_init_at(unsigned address);
int imu_i2c4_read(void *, uint8_t reg, uint8_t *data, size_t length);
int imu_i2c4_write(void *, uint8_t reg, const uint8_t *data, size_t length);
uint32_t imu_i2c4_abort_source(void);
#endif
