# Carrier IMU over I²C

[`robotics_imu`](../examples/platformio/robotics_imu) reads the onboard
**BMI323** on the Beitlab carrier revision 0.11 using the C906 and OS 0.4.0.
No Linux image rebuild is needed. I²C4 belongs to the C906; do not enable
a competing Linux driver for that controller.

For an **external BMI160 on a standalone Duo S using J3**, use the
separate [BMI160 example and wiring guide](BMI160.md). `robotics_imu`
remains BMI323-specific.

For a beitlabduino `setup()`/`loop()` version, use
[`beitlabduino_imu`](../examples/platformio/beitlabduino_imu) instead of
`robotics_imu` in the commands below. Its default `robotics_board`
environment reads this BMI323 with the same `common/lib/carrier_imu`
decoder, `common/lib/duos_i2c4` transport, wiring, profile and serial
output. Its `milkv` environment (`pio run -e milkv -t upload`) reads an
external BMI160 on a standalone Duo S instead; see [BMI160.md](BMI160.md).
The builder automatically includes the transport when the carrier decoder
library is selected.

| Connection | Carrier wiring |
| --- | --- |
| SCL | B1, Duo S J4 pin 50 |
| SDA | B2, Duo S J4 pin 48 |
| Logic | 1.8 V at J4, translated to the onboard 3.3 V bus by PCA9306 |
| Sensor | U10 BMI323, seven-bit address `0x68`, chip ID `0x43` |
| Interrupt | Not used; no setup on A20/INT1 |

No external IMU jumpers are needed. Older carriers with BMI160 are
unsupported: a wrong chip ID is rejected before writing sensor settings.
Address `0x69` is not probed; the carrier straps select `0x68`.

## Upload and see readings

Keep the E-stop open and drives disabled. Upload replaces any robot
firmware on the C906. The example does not initialize PWM, SPI, motor
enables, servos or LED, but cannot guarantee output hardware left by
previous firmware. Follow [carrier power/lock instructions](FLASH.md#7-on-the-carrier-lock-before-the-robot-can-move).

On the Linux x86_64 PC (or WSL2), from the repository:

```sh
git pull --ff-only
scripts/fetch-freertos.sh            # once, if not already completed
export PIO_DUO_HOST=beitlab-milkv-duo.local  # your working SSH host/IP
export PIO_DUO_USER=beitlab
cd examples/platformio/robotics_imu
pio run -t upload
pio run -t rtmonitor
```

If locked, follow the E-stop instructions before running
`sudo -n /usr/libexec/playground/rtos-control unlock` in the board's SSH
session. Keep the E-stop open throughout this test.

Expect a `BMI323 ready` banner and lines shaped like this **illustrative
output**, not a captured hardware measurement:

```text
RTD: imu a_mg=12,-8,998 g_mdps=122,-61,0 temp_mC=24500
```

Groups contain sensor X/Y/Z. `a_mg` uses 1000 units per g; `g_mdps` uses
1000 units per degree/second; `temp_mC` uses 1000 units per °C. Stationary
readings should show roughly one g on the upward axis and near-zero gyro.
No robot-axis rotation, calibration or fusion is applied. The sensor runs
at 100 Hz, ±4 g and ±2000 °/s; the latest sample is printed about every
100 ms. Invalid/unavailable data produces no fabricated zero samples.

For **physical serial**, use a **3.3 V USB–TTL adapter on J22, 115200 8N1**:
adapter RX to board TX, with common ground. Do not connect adapter power
to the carrier. Readings have `RT:` prefixes and share the Linux console.
UART0 and login remain intact. The SSH monitor uses the C906 dump ring
and requires no adapter. Ctrl-C leaves acquisition running.

## beitlabduino API

The optional `beitlabduino_imu` module performs initialization and unit
conversion in the caller's task. The profile's I²C4 route selects the
sensor at run time, so the same application code runs on both boards:

```ini
[env:robotics_board]          ; carrier BMI323
custom_profile = beitlab_robotics_rev011_imu
custom_libs = beitlabduino beitlabduino_imu

[env:milkv]                   ; standalone Duo S, BMI160 on J3
custom_profile = duos_bare_bmi160_j3
custom_libs = beitlabduino beitlabduino_imu
```

The builder adds `carrier_imu`, `beitlabduino_bmi160` and `duos_i2c4`
automatically; older `custom_libs = beitlabduino carrier_imu
beitlabduino_imu` lines keep working. Any other profile is refused before
bus access. Include `beitlabduino.h` and `beitlabduino_imu.h` in your
application.

| API | Result |
| --- | --- |
| `bl_imu_begin()` | 0 on success, `BL_IMU_ERROR` (-1) on failure; allows explicit retry |
| `bl_imu_read(&sample)` | `BL_IMU_SAMPLE_READY` (1), `BL_IMU_NOT_READY` (0), or `BL_IMU_ERROR` (-1) |
| `bl_imu_sensor()` | `"BMI323 carrier I2C4 0x68"`, `"BMI160 J3 I2C4 0x68"`/`0x69`, or NULL before a successful begin |

`bl_imu_sample_t` has `accel_mg[3]`, `gyro_mdps[3]` and `temperature_mc`
in the units above. Print it only when the result **equals**
`BL_IMU_SAMPLE_READY`; output is unchanged on other results. Initialization
waits about 160 ms. Fault diagnostics go through `bl_print`; after a read
error call `bl_imu_begin()` again. The complete example retries once per
second and also reinitializes after a second without fresh data.
`bl_delay(100)` keeps output near 10 Hz. No background task is created.

## Ownership and failure handling

The dedicated profile inherits carrier reservations and grants only I²C4
address `0x68`. B1/B2 remain unavailable as GPIO outputs. The bare-Duo
and normal carrier profiles gain no I²C permission. There are no accesses
to the ADC, PCA9685, display or All Call addresses. Controller and address
policy are checked before any register or pinmux access.

Stock U-Boot also routes I²C4 to VIVO_D1/B20 and VIVO_D0/B21, used for
motor PWM/encoder CS. With I²C disabled, the example makes those old I²C
routes **GPIO inputs**, then selects ADC3/B1 and ADC2/B2. It changes only
existing I²C selections, preserving other functions, and never drives
those GPIO outputs or rewrites GPIO data. This prevents I²C traffic being
mirrored onto the old boot routes.

The driver uses pinned SDK DesignWare definitions and its 100 MHz timing
convention, with conservative approximately 100 kHz bus timing. No system
clock/reset or IRQ setup is changed. Transfers use repeated START and one
final STOP, strip two dummy bytes and decode little-endian words. Polls
and abort/disable cleanup are bounded; retries include delays. No GPIO
bus recovery is attempted. Chip identification precedes soft reset, which
leaves sensor interrupt outputs off; configuration is read back.

Initialization errors retry once per second. `chip` is the observed chip
ID, `err` the sensor error register, and `abort` the DesignWare abort
source. Abort bit 0 means the address was not acknowledged. Check carrier
power, U10 population/strap, translator levels and B1/B2 routing. A
controller probe error can indicate a different OS/clock setup.

Cross-builds and ELF checks pass. Host tests cover decoding, scaling,
wrong devices, faults, policy refusal with no register accesses, route
isolation, NACKs and timeouts. The shared transport's multi-byte read
deadlock, found and fixed on a standalone BMI160 (robot-72), also affected
every BMI323 read, which is at least four bytes; the fix applies to the
carrier unchanged. Physical carrier readings and bus timing measurements
remain pending. See [sdk-notes.md](sdk-notes.md).

A failed BMI323 initialization or read prints `i2c=` with the transport
code: `-2` with `abort=0` is a timeout with no NACK (the bus is held, e.g.
an unpowered carrier translator, or the `robotics_board` build on a bare
Duo S, which needs `-e milkv`), and `-3` is an aborted transfer such as a
NACK. A second line names the likely cause.

Sources: [Bosch BMI323 datasheet, BST-BMI323-DS000-13](https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bmi323-ds000.pdf),
[Milk-V Duo S pinout](https://milkv.io/docs/duo/getting-started/duos#header-j4).
