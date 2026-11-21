/* SPDX-License-Identifier: MIT */
/* Carrier BMI323, polling I2C only; no motors, servos, LED or INT1 GPIO. */
#include <stdint.h>
#include "FreeRTOS.h"
#include "task.h"
#include "playground.h"
#include "pg_profile.h"
#include "bmi323.h"
#include "i2c4.h"

static void delay_ms(void *unused, unsigned ms)
{
	(void)unused;
	vTaskDelay(pdMS_TO_TICKS(ms) ? pdMS_TO_TICKS(ms) : 1);
}

static void imu_task(void *unused)
{
	struct bmi323 imu = {.io = {NULL, imu_i2c4_read, imu_i2c4_write, delay_ms}};
	struct bmi323_sample sample;
	unsigned missed = 0;
	int rc;

	(void)unused;
	pg_print("robotics IMU profile %s", pg_profile_name());
	pg_print("BMI323 I2C4 0x68, B1=SCL B2=SDA; polling only; J22 115200 8N1");
	delay_ms(NULL, 100); /* Carrier supply/translator settling before first attempt. */
	for (;;) {
		if (!imu.ready) {
			rc = imu_i2c4_init();
			if (rc == IMU_I2C_DENIED) {
				pg_print("IMU refused: select beitlab_robotics_rev011_imu; hardware left untouched");
				vTaskDelete(NULL);
				return;
			}
			if (!rc)
				rc = bmi323_start(&imu);
			if (rc) {
				pg_print("IMU init error=%d chip=%x err=%x abort=%x; retry in 1 s",
					 rc, (unsigned)imu.chip_id, (unsigned)imu.errors,
					 (unsigned)imu_i2c4_abort_source());
				delay_ms(NULL, 1000);
				continue;
			}
			missed = 0;
			pg_print("BMI323 ready: +/-4g +/-2000dps, sample 100Hz, print 10Hz");
			pg_print("Units: a_mg=0.001g, g_mdps=0.001deg/s, temp_mC=0.001C");
		}
		rc = bmi323_read_sample(&imu, &sample);
		if (!rc) {
			missed = 0;
			pg_print("imu a_mg=%d,%d,%d g_mdps=%d,%d,%d temp_mC=%d",
				 (int)bmi323_accel_mg(sample.accel[0]),
				 (int)bmi323_accel_mg(sample.accel[1]),
				 (int)bmi323_accel_mg(sample.accel[2]),
				 (int)bmi323_gyro_mdps(sample.gyro[0]),
				 (int)bmi323_gyro_mdps(sample.gyro[1]),
				 (int)bmi323_gyro_mdps(sample.gyro[2]),
				 (int)bmi323_temperature_mc(sample.temperature));
		} else if (rc < 0 || ++missed >= 10) {
			pg_print("IMU data error=%d err=%x abort=%x; reinitializing",
				 rc, (unsigned)imu.errors, (unsigned)imu_i2c4_abort_source());
			imu.ready = 0;
			delay_ms(NULL, 1000);
		}
		delay_ms(NULL, 100);
	}
}

void playground_app_init(void)
{
	if (xTaskCreate(imu_task, "imu", configMINIMAL_STACK_SIZE * 4, NULL,
			tskIDLE_PRIORITY + 1, NULL) != pdPASS)
		pg_print("IMU task creation failed");
}
