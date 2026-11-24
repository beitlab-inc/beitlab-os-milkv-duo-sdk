# beitlabduino_bmi160_j3
Standalone Milk-V Duo S with an external BMI160, no robotics carrier.
Uses beitlabduino setup()/loop() and `bl_bmi160_begin()`/`bl_bmi160_read()`.
J3: SCL pin 3/B20, SDA pin 5/B21, 3V3 pin 1, GND pin 6; 3.3 V logic only.
CS/CSB high for I2C; SDO low selects 0x68, high selects 0x69; both are checked.
Selects `duos_bare_bmi160_j3`, inheriting the bare-Duo profile.
From this folder on the PC: `pio run -t upload`, then `pio run -t rtmonitor`.
Serial output is also on J22 at 115200 8N1; no IMU interrupt wire is required.
Prints acceleration (mg), gyro (mdps) and temperature (mC) at about 10 Hz.
See [BMI160.md](../../../docs/BMI160.md); the shared BMI160 driver reads on robot-72 through `beitlabduino_imu -e milkv`; this example itself is untested on hardware.
