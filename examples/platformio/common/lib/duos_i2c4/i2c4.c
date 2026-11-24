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
#define BUDGET 1000u /* Shared transaction budget, 10 us between unsuccessful polls. */
#define CMD_RESTART (1u << 10) /* SDK linux_5.10 i2c-designware core DATA_CMD. */
#define ENABLE_ABORT (1u << 1)
#define COMP_TYPE_OFFSET 0xfc
#define COMP_TYPE_VALUE 0x44570140
#define GPIO_DDR 0x4

static int ready;
static uint32_t abort_source;
static unsigned target_address;
static struct imu_i2c4_init_state init_state;
static struct imu_i2c4_fault fault;

static void record_fault(unsigned stage, unsigned index)
{
	fault.stage = stage;
	fault.index = index;
	fault.status = READ(ic_status);
	fault.raw_intr = READ(ic_raw_intr_stat);
	fault.txflr = READ(ic_txflr);
	fault.rxflr = READ(ic_rxflr);
	fault.enable_status = READ(ic_enable_status);
}

static int allowed(unsigned address)
{
	enum pg_i2c4_route route = pg_profile_i2c4_route();
	return (route == PG_I2C4_CARRIER || route == PG_I2C4_J3) &&
	       !pg_profile_resource("i2c4") && !pg_profile_i2c_address(4, address);
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
	int rc;

	WRITE(ic_enable, 0);
	rc = wait_for(REG(ic_enable_status), IC_ENABLE, 0, &budget);
	if (rc != IMU_I2C_TIMEOUT || !(READ(ic_status) & IC_STATUS_MA))
		return rc;
	/* A master holding the bus can ignore disable, including after a
	 * C906 reload. ABORT requires ENABLE to be set in an earlier write.
	 * Wait ten 100-kHz periods before aborting, then bound both polls.
	 * Never reset clocks or drive GPIOs to recover a stuck bus. */
	WRITE(ic_enable, IC_ENABLE);
	arch_usleep(100);
	WRITE(ic_enable, IC_ENABLE | ENABLE_ABORT);
	budget = BUDGET;
	while (READ(ic_enable) & ENABLE_ABORT) {
		if (!budget--)
			return IMU_I2C_TIMEOUT;
		arch_usleep(10);
	}
	(void)READ(ic_clr_intr); /* Discard the intentional abort interrupt. */
	if (READ(ic_status) & IC_STATUS_MA) {
		uint32_t target = READ(ic_tar), con = READ(ic_con);
		/* On robot-72, ABORT self-clears without releasing an empty-FIFO
		 * master. Complete it with one read-only RESTART/READ/STOP. Never
		 * send register data, change the target, or address a device the
		 * current profile does not allow. A NACK also terminates the read. */
		if (target > 0x7f || !allowed(target) ||
		    (con & (IC_CON_MM | IC_CON_RE)) != (IC_CON_MM | IC_CON_RE) ||
		    (READ(ic_status) & (IC_STATUS_TFE | IC_STATUS_TFNF)) !=
		    (IC_STATUS_TFE | IC_STATUS_TFNF))
			return IMU_I2C_TIMEOUT;
		WRITE(ic_enable, IC_ENABLE);
		arch_usleep(100);
		WRITE(ic_cmd_data, CMD_RESTART | IC_CMD | IC_STOP);
		budget = BUDGET;
		rc = wait_for(REG(ic_raw_intr_stat), IC_STOP_DET, IC_STOP_DET, &budget);
		if (rc && rc != IMU_I2C_ABORT)
			return rc;
		(void)READ(ic_clr_intr);
	}
	WRITE(ic_enable, 0);
	budget = BUDGET;
	return wait_for(REG(ic_enable_status), IC_ENABLE, 0, &budget);
}

static void isolate_boot_route(unsigned reg, unsigned mask, unsigned function,
			       unsigned gpio_function, unsigned gpio_bit)
{
	uintptr_t mux = PINMUX_BASE + reg;

	if ((mmio_read_32(mux) & mask) != function)
		return;
	/* Make only the matching old I2C route high-impedance. Never drive
	 * GPIO DR or replace another function left by previous firmware. */
	taskENTER_CRITICAL();
	mmio_write_32(CVI_GPIOB_BASE + GPIO_DDR,
		     mmio_read_32(CVI_GPIOB_BASE + GPIO_DDR) & ~(1u << gpio_bit));
	mmio_clrsetbits_32(mux, mask, gpio_function);
	taskEXIT_CRITICAL();
}

int imu_i2c4_init_at(unsigned address)
{
	int rc;

	ready = 0;
	abort_source = 0;
	init_state = (struct imu_i2c4_init_state){0};
	if (!allowed(address))
		return IMU_I2C_DENIED;
	target_address = address;
	if (mmio_read_32(I2C4_BASE + COMP_TYPE_OFFSET) != COMP_TYPE_VALUE)
		return IMU_I2C_CONTROLLER;
	/* Stop any previous transaction before changing routes. All waits bounded. */
	WRITE(ic_intr_mask, 0);
	(void)READ(ic_clr_intr);
	rc = disable();
	if (rc) {
		init_state.enable = READ(ic_enable);
		init_state.enable_status = READ(ic_enable_status);
		init_state.status = READ(ic_status);
		return rc;
	}
	if (pg_profile_i2c4_route() == PG_I2C4_CARRIER) {
		isolate_boot_route(FMUX_GPIO_FUNCSEL_VIVO_D1, FMUX_GPIO_FUNCSEL_VIVO_D1_MASK,
			VIVO_D1__IIC4_SCL, VIVO_D1__XGPIOB_20, 20);
		isolate_boot_route(FMUX_GPIO_FUNCSEL_VIVO_D0, FMUX_GPIO_FUNCSEL_VIVO_D0_MASK,
			VIVO_D0__IIC4_SDA, VIVO_D0__XGPIOB_21, 21);
		PINMUX_CONFIG(ADC3, IIC4_SCL); /* B1, J4-50, 1.8 V before PCA9306. */
		PINMUX_CONFIG(ADC2, IIC4_SDA); /* B2, J4-48. */
	} else {
		/* Standalone 3.3 V J3: disconnect a stale J4 route from a
		 * previously loaded firmware before enabling this controller. */
		isolate_boot_route(FMUX_GPIO_FUNCSEL_ADC3, FMUX_GPIO_FUNCSEL_ADC3_MASK,
			ADC3__IIC4_SCL, ADC3__XGPIOB_1, 1);
		isolate_boot_route(FMUX_GPIO_FUNCSEL_ADC2, FMUX_GPIO_FUNCSEL_ADC2_MASK,
			ADC2__IIC4_SDA, ADC2__XGPIOB_2, 2);
		PINMUX_CONFIG(VIVO_D1, IIC4_SCL); /* B20, J3-3, 3.3 V. */
		PINMUX_CONFIG(VIVO_D0, IIC4_SDA); /* B21, J3-5, 3.3 V. */
	}
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

int imu_i2c4_init(void)
{
	return imu_i2c4_init_at(0x68);
}

static int transfer(uint8_t reg, uint8_t *rx, const uint8_t *tx, size_t length)
{
	unsigned budget = BUDGET, i = 0, stage = 0;
	int rc;

	if (!allowed(target_address))
		return IMU_I2C_DENIED;
	if (!ready || !length || length > 16 || reg > 0x7f || (!rx && !tx))
		return IMU_I2C_ARGUMENT;
	abort_source = 0;
	(void)READ(ic_clr_intr);
	rc = disable();
	if (rc) {
		record_fault(1, 0);
		return rc;
	}
	/* Disabling flushes TX; bound RX draining even on faulty hardware. */
	for (i = 0; i < 64 && (READ(ic_status) & IC_STATUS_RFNE); i++)
		(void)READ(ic_cmd_data);
	if (READ(ic_status) & IC_STATUS_RFNE)
		return IMU_I2C_CONTROLLER;
	WRITE(ic_tar, target_address);
	WRITE(ic_enable, IC_ENABLE);
	stage = 2;
	rc = wait_for(REG(ic_enable_status), IC_ENABLE, IC_ENABLE, &budget);
	if (rc)
		goto done;
	stage = 3;
	rc = wait_for(REG(ic_status), IC_STATUS_TFNF, IC_STATUS_TFNF, &budget);
	if (rc)
		goto done;
	WRITE(ic_cmd_data, reg); /* Address byte has no STOP. */
	if (rx) {
		unsigned sent = 0;
		/* The controller completes a received byte only once the next
		 * command (or this one's STOP) decides its ACK/NACK. Waiting for
		 * byte i before queuing read i+1 holds the bus forever, so keep
		 * one read queued ahead; at most two bytes are ever in flight. */
		for (i = 0; i < length; i++) {
			while (sent < length && sent < i + 2) {
				uint32_t cmd = IC_CMD;
				if (sent == 0)
					cmd |= CMD_RESTART;
				if (sent + 1 == length)
					cmd |= IC_STOP;
				stage = 4;
				rc = wait_for(REG(ic_status), IC_STATUS_TFNF, IC_STATUS_TFNF, &budget);
				if (rc)
					goto done;
				WRITE(ic_cmd_data, cmd);
				sent++;
			}
			stage = 5;
			rc = wait_for(REG(ic_status), IC_STATUS_RFNE, IC_STATUS_RFNE, &budget);
			if (rc)
				goto done;
			rx[i] = (uint8_t)READ(ic_cmd_data);
		}
	} else {
		for (i = 0; i < length; i++) {
			uint32_t cmd = tx[i];
			if (i + 1 == length)
				cmd |= IC_STOP;
			stage = 4;
			rc = wait_for(REG(ic_status), IC_STATUS_TFNF, IC_STATUS_TFNF, &budget);
			if (rc)
				goto done;
			WRITE(ic_cmd_data, cmd);
		}
	}
	stage = 6;
	rc = wait_for(REG(ic_raw_intr_stat), IC_STOP_DET, IC_STOP_DET, &budget);
	if (!rc) {
		stage = 7;
		rc = wait_for(REG(ic_status), IC_STATUS_MA | IC_STATUS_TFE, IC_STATUS_TFE, &budget);
	}
done:
	if (rc)
		record_fault(stage, i);
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

struct imu_i2c4_init_state imu_i2c4_last_init_state(void)
{
	return init_state;
}

struct imu_i2c4_fault imu_i2c4_last_fault(void)
{
	return fault;
}
