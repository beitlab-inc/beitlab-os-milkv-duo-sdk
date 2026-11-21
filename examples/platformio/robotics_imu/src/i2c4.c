/* SPDX-License-Identifier: MIT */
/* Pinned SDK top_reg.h, hal_dw_i2c.h and cv181x_pinlist_swconfig.h define
 * the controller, offsets and routes. Polling replaces the vendor HAL's
 * unbounded write loop, STOP-per-read-byte and fixed I2C3 IRQ registration.
 * No global clocks/resets, PWM, SPI, IRQ or GPIO outputs are initialized. */
#include <stddef.h>
#include <stdint.h>
#include "FreeRTOS.h"
#include "task.h"
#include "mmio.h"
#include "gpio.h"
#include "top_reg.h"
#include "pinctrl.h"
#include "arch_sleep.h"
#include "hal_dw_i2c.h"
#include "pg_profile.h"
#include "i2c4.h"

#define REG(member) ((uintptr_t)I2C4_BASE + offsetof(struct i2c_regs, member))
#define READ(member) mmio_read_32(REG(member))
#define WRITE(member, value) mmio_write_32(REG(member), (value))
#define ADDRESS 0x68
#define BUDGET 1000u /* Shared transaction budget, 10 us between unsuccessful polls. */
#define CMD_RESTART (1u << 10) /* SDK linux_5.10 i2c-designware core DATA_CMD. */
#define ENABLE_ABORT (1u << 1)
#define COMP_TYPE_OFFSET 0xfc
#define COMP_TYPE_VALUE 0x44570140
#define GPIO_DDR 0x4

static int ready;
static uint32_t abort_source;

static int allowed(void)
{
	return !pg_profile_resource("i2c4") && !pg_profile_i2c_address(4, ADDRESS);
}

static int wait_for(uintptr_t address, uint32_t mask, uint32_t value, unsigned *budget)
{
	while (*budget) {
		--*budget;
		if (READ(ic_raw_intr_stat) & IC_TX_ABRT) {
			abort_source = READ(ic_tx_abrt_source);
			(void)READ(ic_clr_tx_abrt);
			return IMU_I2C_ABORT;
		}
		if ((mmio_read_32(address) & mask) == value)
			return 0;
		arch_usleep(10);
	}
	return IMU_I2C_TIMEOUT;
}

static int disable(void)
{
	unsigned budget = BUDGET;

	WRITE(ic_enable, 0);
	return wait_for(REG(ic_enable_status), IC_ENABLE, 0, &budget);
}

static void isolate_boot_route(unsigned reg, unsigned mask, unsigned function,
			       unsigned gpio_function, unsigned gpio_bit)
{
	uintptr_t mux = PINMUX_BASE + reg;

	if ((mmio_read_32(mux) & mask) != function)
		return;
	/* The stock bootloader mirrors I2C4 on B20/B21 (motor PWM/encoder CS).
	 * Make those old routes high-impedance before using B1/B2. Do not drive
	 * GPIO DR, or replace a different function left by another firmware. */
	taskENTER_CRITICAL();
	mmio_write_32(CVI_GPIOB_BASE + GPIO_DDR,
		     mmio_read_32(CVI_GPIOB_BASE + GPIO_DDR) & ~(1u << gpio_bit));
	mmio_clrsetbits_32(mux, mask, gpio_function);
	taskEXIT_CRITICAL();
}

int imu_i2c4_init(void)
{
	int rc;

	ready = 0;
	abort_source = 0;
	if (!allowed())
		return IMU_I2C_DENIED;
	if (mmio_read_32(I2C4_BASE + COMP_TYPE_OFFSET) != COMP_TYPE_VALUE)
		return IMU_I2C_CONTROLLER;
	/* Stop any previous transaction before changing routes. All waits bounded. */
	WRITE(ic_intr_mask, 0);
	(void)READ(ic_clr_intr);
	rc = disable();
	if (rc)
		return rc;
	isolate_boot_route(FMUX_GPIO_FUNCSEL_VIVO_D1, FMUX_GPIO_FUNCSEL_VIVO_D1_MASK,
		VIVO_D1__IIC4_SCL, VIVO_D1__XGPIOB_20, 20);
	isolate_boot_route(FMUX_GPIO_FUNCSEL_VIVO_D0, FMUX_GPIO_FUNCSEL_VIVO_D0_MASK,
		VIVO_D0__IIC4_SDA, VIVO_D0__XGPIOB_21, 21);
	PINMUX_CONFIG(ADC3, IIC4_SCL); /* B1, J4-50, 1.8 V before PCA9306. */
	PINMUX_CONFIG(ADC2, IIC4_SDA); /* B2, J4-48. */
	WRITE(ic_con, IC_CON_MM | IC_CON_SPD_SS | IC_CON_RE | IC_CON_SD);
	/* SDK IC_CLK is MHz (100). >=5 us high/low gives <=100 kHz. */
	WRITE(ic_ss_scl_hcnt, IC_CLK * 5);
	WRITE(ic_ss_scl_lcnt, IC_CLK * 5);
	WRITE(ic_sda_hold, IC_CLK * 3 / 10); /* 300 ns at the pinned 100 MHz clock. */
	WRITE(ic_rx_tl, 0);
	WRITE(ic_tx_tl, 0);
	ready = 1;
	return 0;
}

static int transfer(uint8_t reg, uint8_t *rx, const uint8_t *tx, size_t length)
{
	unsigned budget = BUDGET, i;
	int rc;

	if (!allowed())
		return IMU_I2C_DENIED;
	if (!ready || !length || length > 16 || reg > 0x7f || (!rx && !tx))
		return IMU_I2C_ARGUMENT;
	abort_source = 0;
	(void)READ(ic_clr_intr);
	rc = disable();
	if (rc)
		return rc;
	/* Disabling flushes TX; bound RX draining even on faulty hardware. */
	for (i = 0; i < 64 && (READ(ic_status) & IC_STATUS_RFNE); i++)
		(void)READ(ic_cmd_data);
	if (READ(ic_status) & IC_STATUS_RFNE)
		return IMU_I2C_CONTROLLER;
	WRITE(ic_tar, ADDRESS);
	WRITE(ic_enable, IC_ENABLE);
	rc = wait_for(REG(ic_enable_status), IC_ENABLE, IC_ENABLE, &budget);
	if (rc)
		goto done;
	rc = wait_for(REG(ic_status), IC_STATUS_TFNF, IC_STATUS_TFNF, &budget);
	if (rc)
		goto done;
	WRITE(ic_cmd_data, reg); /* Address byte has no STOP. */
	for (i = 0; i < length; i++) {
		uint32_t cmd = rx ? IC_CMD : tx[i];
		if (rx && i == 0)
			cmd |= CMD_RESTART;
		if (i + 1 == length)
			cmd |= IC_STOP;
		rc = wait_for(REG(ic_status), IC_STATUS_TFNF, IC_STATUS_TFNF, &budget);
		if (rc)
			goto done;
		WRITE(ic_cmd_data, cmd);
		if (rx) {
			rc = wait_for(REG(ic_status), IC_STATUS_RFNE, IC_STATUS_RFNE, &budget);
			if (rc)
				goto done;
			rx[i] = (uint8_t)READ(ic_cmd_data);
		}
	}
	rc = wait_for(REG(ic_raw_intr_stat), IC_STOP_DET, IC_STOP_DET, &budget);
	if (!rc)
		rc = wait_for(REG(ic_status), IC_STATUS_MA | IC_STATUS_TFE, IC_STATUS_TFE, &budget);
done:
	if (rc) {
		unsigned cleanup = BUDGET;
		/* Let ABORT complete before disabling; a stuck SCL may prevent it.
		 * Cleanup has its own finite budget and never resets other devices. */
		WRITE(ic_enable, IC_ENABLE | ENABLE_ABORT);
		while (cleanup && (READ(ic_enable) & ENABLE_ABORT)) {
			--cleanup;
			arch_usleep(10);
		}
		if (!cleanup)
			ready = 0;
	}
	(void)READ(ic_clr_intr);
	{
		int stopped = disable();
		if (stopped) {
			ready = 0;
			if (!rc)
				rc = stopped;
		}
	}
	return rc;
}

int imu_i2c4_read(void *unused, uint8_t reg, uint8_t *data, size_t length)
{
	(void)unused;
	return data ? transfer(reg, data, NULL, length) : IMU_I2C_ARGUMENT;
}

int imu_i2c4_write(void *unused, uint8_t reg, const uint8_t *data, size_t length)
{
	(void)unused;
	return data ? transfer(reg, NULL, data, length) : IMU_I2C_ARGUMENT;
}

uint32_t imu_i2c4_abort_source(void)
{
	return abort_source;
}
