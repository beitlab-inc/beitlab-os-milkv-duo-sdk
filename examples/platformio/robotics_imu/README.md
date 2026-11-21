# robotics_imu
Polls the carrier revision 0.11 BMI323 at I2C4 address 0x68 over B1/SCL and B2/SDA.
Uses `beitlab_robotics_rev011_imu`; only the BMI323 address is granted.
No SPI, PWM, LED or IMU interrupt; the old boot I2C routes are isolated as inputs.
Keep the carrier E-stop open during this diagnostic; see [IMU.md](../../../docs/IMU.md).
From this folder on the PC: `pio run -t upload`.
View over SSH: `pio run -t rtmonitor`; Ctrl-C leaves acquisition running.
Physical serial: J22, 115200 8N1, 3.3 V USB–TTL; readings have the `RT:` prefix.
Prints xyz acceleration in mg, xyz angular rate in mdps and temperature in mC at 10 Hz.
Build/protocol/MMIO tests pass; physical sensor readings remain unverified.
