# beitlabduino_imu
Reads the board's IMU through beitlabduino `setup()` and `loop()`; the selected environment picks the sensor.
Uses `bl_imu_begin()`, `bl_imu_read()`, `bl_imu_sensor()`, `bl_print()` and `bl_delay()`.

| Environment | Board | Sensor | Profile |
| --- | --- | --- | --- |
| `robotics_board` (default) | Duo S on the robotics carrier rev 0.11 | onboard BMI323, I2C4 0x68, B1/SCL, B2/SDA | `beitlab_robotics_rev011_imu` |
| `milkv` | standalone Duo S, carrier disconnected | external BMI160 on J3: pin 3 B20/SCL, pin 5 B21/SDA, 0x68 or 0x69 | `duos_bare_bmi160_j3` |

Carrier, from this folder on the PC: `pio run -t upload`; keep the carrier E-stop open.
Standalone Milk-V with BMI160: `pio run -e milkv -t upload`; wire it as in [BMI160.md](../../../docs/BMI160.md).
Never upload `milkv` to a board on the carrier: J3's B20 is a carrier motor PWM pad.
Monitor over SSH: `pio run -t rtmonitor`; Ctrl-C leaves sampling running.
Physical serial: J22, 115200 8N1, 3.3 V USB–TTL; output has `RT:` prefixes.
The banner names the detected sensor, e.g. `beitlabduino IMU ready: BMI160 J3 I2C4 0x68, print 10Hz`.
Prints XYZ acceleration (mg), angular rate (mdps) and temperature (mC) at 10 Hz. No INT1, SPI, PWM or LED.
See [IMU.md](../../../docs/IMU.md) for API/errors. `milkv` reads a BMI160 on robot-72; carrier BMI323 readings remain pending.
