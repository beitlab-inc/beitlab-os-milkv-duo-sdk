/* SPDX-License-Identifier: MIT */
#ifndef EXAMPLE_I2C4_H
#define EXAMPLE_I2C4_H
#include <stddef.h>
#include <stdint.h>
/* Profile-selected carrier/J3 I2C4 route, authorized seven-bit targets only.
 * Task context, one caller. Bounded polling, no IRQ/DMA or GPIO recovery. */
enum { IMU_I2C_DENIED = -1, IMU_I2C_TIMEOUT = -2, IMU_I2C_ABORT = -3,
       IMU_I2C_CONTROLLER = -4, IMU_I2C_ARGUMENT = -5 };
struct imu_i2c4_init_state {
	uint32_t enable, enable_status, status;
};
/* Snapshot on failed controller shutdown; getter performs no MMIO. */
struct imu_i2c4_init_state imu_i2c4_last_init_state(void);
int imu_i2c4_init(void);
/* The no-argument initializer retains the carrier's default address 0x68. */
int imu_i2c4_init_at(unsigned address);
int imu_i2c4_read(void *, uint8_t reg, uint8_t *data, size_t length);
int imu_i2c4_write(void *, uint8_t reg, const uint8_t *data, size_t length);
uint32_t imu_i2c4_abort_source(void);
/* Snapshot of the last failed transfer, taken before abort cleanup. stage:
 * 1 shutdown, 2 enable, 3 TX space for register, 4 TX space for byte,
 * 5 RX byte, 6 STOP, 7 master idle; index is the data byte. No MMIO. */
struct imu_i2c4_fault {
	uint32_t stage, index, status, raw_intr, txflr, rxflr, enable_status;
};
struct imu_i2c4_fault imu_i2c4_last_fault(void);
#endif
