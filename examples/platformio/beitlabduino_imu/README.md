# beitlabduino_imu
Reads the carrier revision 0.11 BMI323 through beitlabduino `setup()` and `loop()`.
Uses `bl_imu_begin()`, `bl_imu_read()`, `bl_print()` and `bl_delay()`.
I2C only: I2C4 address 0x68, B1/SCL and B2/SDA; no INT1, SPI, PWM or LED.
Selects `beitlab_robotics_rev011_imu`; keep the carrier E-stop open.
From this folder on the PC: `pio run -t upload`.
Monitor over SSH: `pio run -t rtmonitor`; Ctrl-C leaves sampling running.
Physical serial: J22, 115200 8N1, 3.3 V USB–TTL; output has `RT:` prefixes.
Prints XYZ acceleration (mg), angular rate (mdps) and temperature (mC) at 10 Hz.
See [IMU.md](../../../docs/IMU.md) for API/errors; builds/tests pass, physical readings remain pending.
