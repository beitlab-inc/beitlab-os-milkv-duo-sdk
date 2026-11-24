"""Run actual IMU/polling-driver code against protocol and MMIO models, no hardware."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / 'examples/platformio/common/lib/carrier_imu'
TRANSPORT = REPO / 'examples/platformio/common/lib/duos_i2c4'
SPEC = importlib.util.spec_from_file_location('imu_profiles', REPO / 'firmware/profiles.py')
profiles = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profiles)


@unittest.skipUnless(shutil.which('cc'), 'needs a host C compiler')
class IMUTest(unittest.TestCase):
    def compile_and_run(self, root, sources):
        binary = root / 'test'
        command = ['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                   '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                   '-I' + str(root), '-I' + str(SOURCE), '-I' + str(TRANSPORT), '-I' + str(REPO / 'firmware/include'),
                   *map(str, sources), str(root / 'test.c'), '-o', str(binary)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_bmi323_protocol_configuration_scaling_and_error_handling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'test.c').write_text(SENSOR_HARNESS)
            self.compile_and_run(root, [SOURCE / 'bmi323.c'])

    def test_i2c_policy_routes_restart_stop_abort_and_bounded_timeout(self):
        for profile in ('duos_bare', 'beitlab_robotics_rev011', 'beitlab_robotics_rev011_imu', 'duos_bare_bmi160_j3'):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                profiles.generate_header(profiles.load_profile(profile), root / 'pg_profile_generated.h')
                for name, content in HEADERS.items():
                    (root / name).write_text(content)
                (root / 'test.c').write_text(DRIVER_HARNESS)
                self.compile_and_run(root, [TRANSPORT / 'i2c4.c', REPO / 'firmware/runtime/pg_profile.c'])

    def test_beitlabduino_api_samples_errors_and_reinitialization(self):
        module = REPO / 'examples/platformio/common/lib/beitlabduino_imu'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'beitlabduino.h').write_text(
                '#include <stdint.h>\nvoid bl_delay(uint32_t);\n'
                'void pg_print(const char *, ...);\n#define bl_print pg_print\n')
            shutil.copyfile(module / 'beitlabduino_imu.h', root / 'beitlabduino_imu.h')
            shutil.copyfile(REPO / 'examples/platformio/common/lib/beitlabduino_bmi160/beitlabduino_bmi160.h',
                            root / 'beitlabduino_bmi160.h')
            (root / 'test.c').write_text(SENSOR_HARNESS.split('int main(void)', 1)[0] + WRAPPER_HARNESS)
            self.compile_and_run(root, [SOURCE / 'bmi323.c', module / 'beitlabduino_imu.c'])


SENSOR_HARNESS = r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include "bmi323.h"
static uint16_t regs[128];
static unsigned writes, delays, burst_reads;
static int fail_bus, mismatch, fatal;

static int read_bus(void *ctx, uint8_t reg, uint8_t *bytes, size_t length)
{
    size_t i;
    (void)ctx;
    assert(length >= 4 && length <= 16 && !(length & 1));
    if (fail_bus) return -1;
    bytes[0] = 0xa5; bytes[1] = 0x5a; /* Not sensor data! */
    for (i = 2; i < length; i += 2) {
        uint16_t value = regs[reg + (i - 2) / 2];
        if (mismatch && reg == 0x20) value ^= 0x10;
        bytes[i] = value; bytes[i + 1] = value >> 8;
    }
    if (reg == 2) regs[2] &= ~1u; /* POR is clear on read. */
    if (reg == 3) { assert(length == 16); burst_reads++; }
    return 0;
}
static int write_bus(void *ctx, uint8_t reg, const uint8_t *bytes, size_t length)
{
    uint16_t value;
    (void)ctx;
    if (fail_bus) return -1;
    assert(length == 2); /* Writes have no dummy bytes, low byte first. */
    value = (uint16_t)bytes[0] | (uint16_t)bytes[1] << 8;
    writes++;
    if (reg == 0x7e) {
        assert(value == 0xdeaf);
        memset(regs, 0, sizeof(regs));
        regs[0] = 0x43; regs[2] = 1; regs[1] = fatal ? 1 : 0;
    } else {
        assert(reg == 0x20 || reg == 0x21); /* No interrupt or other-device setup. */
        regs[reg] = value;
    }
    return 0;
}
static void delay_bus(void *ctx, unsigned ms)
{
    (void)ctx; delays += ms;
}
int main(void)
{
    struct bmi323 imu = {.io = {NULL, read_bus, write_bus, delay_bus}};
    struct bmi323_sample sample, sentinel;
    unsigned i, old_writes;
    memset(&sentinel, 0x55, sizeof(sentinel));
    sample = sentinel;
    assert(bmi323_start(NULL) == BMI323_CONFIG_ERROR);
    assert(bmi323_read_sample(&imu, &sample) == BMI323_CONFIG_ERROR);
    regs[0] = 0xd1; /* BMI160: must refuse before any sensor write. */
    assert(bmi323_start(&imu) == BMI323_WRONG_ID && writes == 0);
    regs[0] = 0x43; fail_bus = 1;
    assert(bmi323_start(&imu) == BMI323_BUS_ERROR && writes == 0);
    fail_bus = 0; fatal = 1;
    assert(bmi323_start(&imu) == BMI323_SENSOR_ERROR && !imu.ready);
    fatal = 0; mismatch = 1;
    assert(bmi323_start(&imu) == BMI323_CONFIG_ERROR && !imu.ready);
    mismatch = 0;
    assert(bmi323_start(&imu) == 0 && imu.ready);
    assert(regs[0x20] == 0x7098 && regs[0x21] == 0x70c8);
    assert(delays >= 60);
    old_writes = writes;
    assert(bmi323_read_sample(&imu, &sample) == BMI323_NOT_READY);
    assert(!memcmp(&sample, &sentinel, sizeof(sample)));
    regs[2] = 0xc0;
    regs[3] = 8192; regs[4] = (uint16_t)-8192; regs[5] = 0;
    regs[6] = 16384; regs[7] = (uint16_t)-16384; regs[8] = 1; regs[9] = 512;
    assert(bmi323_read_sample(&imu, &sample) == 0 && burst_reads == 1);
    assert(sample.accel[0] == 8192 && sample.accel[1] == -8192);
    assert(sample.gyro[0] == 16384 && sample.gyro[1] == -16384);
    assert(sample.temperature == 512 && writes == old_writes);
    assert(bmi323_accel_mg(sample.accel[0]) == 1000);
    assert(bmi323_accel_mg(sample.accel[1]) == -1000);
    assert(bmi323_gyro_mdps(sample.gyro[0]) == 1000000);
    assert(bmi323_gyro_mdps(sample.gyro[1]) == -1000000);
    assert(bmi323_temperature_mc(sample.temperature) == 24000);
    assert(bmi323_temperature_mc(-512) == 22000);
    for (i = 0; i < 7; i++) {
        uint16_t previous = regs[3 + i];
        regs[3 + i] = 0x8000;
        sample = sentinel;
        assert(bmi323_read_sample(&imu, &sample) == BMI323_NOT_READY);
        assert(!memcmp(&sample, &sentinel, sizeof(sample)));
        regs[3 + i] = previous;
    }
    fail_bus = 1; sample = sentinel;
    assert(bmi323_read_sample(&imu, &sample) == BMI323_BUS_ERROR);
    assert(!memcmp(&sample, &sentinel, sizeof(sample)));
    fail_bus = 0; regs[1] = 0x40;
    assert(bmi323_read_sample(&imu, &sample) == BMI323_SENSOR_ERROR && !imu.ready);
    assert(bmi323_start(&imu) == 0);
    regs[2] = 1; /* brownout: never print readings with the old range */
    assert(bmi323_read_sample(&imu, &sample) == BMI323_SENSOR_ERROR && !imu.ready);
    assert(bmi323_accel_mg(INT16_MIN) == -4000);
    assert(bmi323_gyro_mdps(INT16_MIN) == -2000000);
    assert(bmi323_gyro_mdps(INT16_MAX) < 2000000);
    return 0;
}
'''

WRAPPER_HARNESS = r'''
#include <stdarg.h>
#include <stdio.h>
#include "beitlabduino_bmi160.h"
#include "beitlabduino_imu.h"
#include "pg_profile.h"
static int init_error, bmi160_begin_rc, bmi160_read_rc;
static unsigned bus_reads, bmi160_begins, bmi160_reads, bmi160_addr;
static enum pg_i2c4_route route = PG_I2C4_NONE;
static char message[256], previous[256];
static int timeout_bus;
static uint32_t abort_bits = 1;
enum pg_i2c4_route pg_profile_i2c4_route(void) { return route; }
int bl_bmi160_begin(void) { bmi160_begins++; bmi160_addr = bmi160_begin_rc ? 0 : 0x69; return bmi160_begin_rc; }
unsigned bl_bmi160_address(void) { return bmi160_addr; }
int bl_bmi160_read(bl_bmi160_sample_t *sample)
{
    bmi160_reads++;
    if (bmi160_read_rc == BL_BMI160_SAMPLE_READY) {
        sample->accel_mg[0] = 1; sample->accel_mg[1] = 2; sample->accel_mg[2] = 1000;
        sample->gyro_mdps[0] = -4; sample->gyro_mdps[1] = 5; sample->gyro_mdps[2] = 6;
        sample->temperature_mc = 23000;
    }
    return bmi160_read_rc;
}
int imu_i2c4_init(void) { return init_error; }
int imu_i2c4_read(void *ctx, uint8_t reg, uint8_t *data, size_t length)
{
    bus_reads++;
    if (timeout_bus) return -2; /* IMU_I2C_TIMEOUT */
    return read_bus(ctx, reg, data, length);
}
int imu_i2c4_write(void *ctx, uint8_t reg, const uint8_t *data, size_t length)
{
    return write_bus(ctx, reg, data, length);
}
uint32_t imu_i2c4_abort_source(void) { return abort_bits; }
void bl_delay(uint32_t ms) { delay_bus(NULL, ms); }
void pg_print(const char *fmt, ...)
{
    va_list args;
    memcpy(previous, message, sizeof(previous));
    va_start(args, fmt);
    vsnprintf(message, sizeof(message), fmt, args);
    va_end(args);
}
int main(void)
{
    bl_imu_sample_t sample, sentinel;
    unsigned before;
    memset(&sentinel, 0x55, sizeof(sentinel));
    sample = sentinel;
    assert(bl_imu_read(&sample) == BL_IMU_ERROR && bus_reads == 0);
    assert(bl_imu_read(NULL) == BL_IMU_ERROR);
    assert(bl_imu_begin() == BL_IMU_ERROR && bus_reads == 0 && writes == 0 && !bmi160_begins);
    assert(strstr(message, "beitlab_robotics_rev011_imu") && strstr(message, "duos_bare_bmi160_j3"));
    assert(!bl_imu_sensor());

    /* Standalone Duo S: delegate to the J3 BMI160 and never touch the BMI323. */
    route = PG_I2C4_J3; bmi160_begin_rc = -1;
    assert(bl_imu_begin() == BL_IMU_ERROR && bmi160_begins == 1 && !bl_imu_sensor());
    assert(bl_imu_read(&sample) == BL_IMU_ERROR && !bmi160_reads);
    bmi160_begin_rc = 0;
    assert(bl_imu_begin() == 0 && !strcmp(bl_imu_sensor(), "BMI160 J3 I2C4 0x69"));
    bmi160_read_rc = BL_BMI160_NOT_READY;
    assert(bl_imu_read(&sample) == BL_IMU_NOT_READY);
    assert(!memcmp(&sample, &sentinel, sizeof(sample)));
    bmi160_read_rc = BL_BMI160_SAMPLE_READY;
    assert(bl_imu_read(&sample) == BL_IMU_SAMPLE_READY);
    assert(sample.accel_mg[2] == 1000 && sample.gyro_mdps[0] == -4 && sample.temperature_mc == 23000);
    bmi160_read_rc = BL_BMI160_ERROR; before = bmi160_reads;
    assert(bl_imu_read(&sample) == BL_IMU_ERROR && bmi160_reads == before + 1);
    assert(bl_imu_read(&sample) == BL_IMU_ERROR && bmi160_reads == before + 1 && !bl_imu_sensor());
    assert(bus_reads == 0 && writes == 0);

    /* Robotics carrier: BMI323 only. */
    route = PG_I2C4_CARRIER; before = bmi160_begins; sample = sentinel;
    init_error = -1;
    assert(bl_imu_begin() == BL_IMU_ERROR && bus_reads == 0 && writes == 0);
    assert(strstr(message, "select beitlab_robotics_rev011_imu"));
    init_error = -2;
    assert(bl_imu_begin() == BL_IMU_ERROR && bus_reads == 0 && writes == 0);
    assert(strstr(message, "bus error=-2"));
    init_error = 0; regs[0] = 0xd1;
    assert(bl_imu_begin() == BL_IMU_ERROR && writes == 0);
    assert(strstr(message, "chip=d1"));
    /* Bus held low (e.g. unpowered carrier): name the I2C code and the likely cause. */
    timeout_bus = 1; abort_bits = 0;
    assert(bl_imu_begin() == BL_IMU_ERROR && writes == 0);
    assert(strstr(previous, "init error=-1 i2c=-2 chip=0") && strstr(message, "carrier power"));
    timeout_bus = 0; abort_bits = 1;
    regs[0] = 0x43;
    assert(bl_imu_begin() == 0 && delays >= 160);
    assert(!strcmp(bl_imu_sensor(), "BMI323 carrier I2C4 0x68") && bmi160_begins == before);
    assert(bl_imu_read(&sample) == BL_IMU_NOT_READY);
    assert(!memcmp(&sample, &sentinel, sizeof(sample)));
    regs[2] = 0xc0;
    regs[3] = 8192; regs[4] = (uint16_t)-8192; regs[5] = 0;
    regs[6] = 16384; regs[7] = (uint16_t)-16384; regs[8] = 0; regs[9] = 512;
    assert(bl_imu_read(NULL) == BL_IMU_ERROR);
    assert(bl_imu_read(&sample) == BL_IMU_SAMPLE_READY);
    assert(sample.accel_mg[0] == 1000 && sample.accel_mg[1] == -1000 && sample.accel_mg[2] == 0);
    assert(sample.gyro_mdps[0] == 1000000 && sample.gyro_mdps[1] == -1000000);
    assert(sample.gyro_mdps[2] == 0 && sample.temperature_mc == 24000);
    fail_bus = 1; sample = sentinel;
    assert(bl_imu_read(&sample) == BL_IMU_ERROR);
    assert(strstr(message, "read error="));
    assert(!memcmp(&sample, &sentinel, sizeof(sample)));
    fail_bus = 0; before = bus_reads;
    assert(bl_imu_read(&sample) == BL_IMU_ERROR && bus_reads == before);
    assert(bl_imu_begin() == 0);
    regs[1] = 0x40;
    assert(bl_imu_read(&sample) == BL_IMU_ERROR && strstr(message, "err=40"));
    assert(bl_imu_begin() == 0);
    regs[2] = 1;
    assert(bl_imu_read(&sample) == BL_IMU_ERROR);
    assert(!memcmp(&sample, &sentinel, sizeof(sample)));
    return 0;
}
'''


# Minimal host definitions of the SDK interfaces; no SDK checkout needed in CI.
HEADERS = {
    'FreeRTOS.h': '/* host shim */\n',
    'task.h': '#define taskENTER_CRITICAL() ((void)0)\n#define taskEXIT_CRITICAL() ((void)0)\n',
    'mmio.h': '#include <stdint.h>\nuint32_t mmio_read_32(uintptr_t);\nvoid mmio_write_32(uintptr_t, uint32_t);\nvoid mmio_clrsetbits_32(uintptr_t, uint32_t, uint32_t);\n',
    'gpio.h': '#define CVI_GPIOB_BASE 0x03021000u\n',
    'top_reg.h': '#define I2C4_BASE 0x04040000u\n',
    'arch_sleep.h': 'void arch_usleep(unsigned long);\n',
    'pinctrl.h': r'''
#define PINMUX_BASE 0x03001000u
#define FMUX_GPIO_FUNCSEL_VIVO_D1 0x158
#define FMUX_GPIO_FUNCSEL_VIVO_D0 0x15c
#define FMUX_GPIO_FUNCSEL_VIVO_D1_MASK 7
#define FMUX_GPIO_FUNCSEL_VIVO_D0_MASK 7
#define VIVO_D1__IIC4_SCL 7
#define VIVO_D0__IIC4_SDA 7
#define VIVO_D1__XGPIOB_20 3
#define VIVO_D0__XGPIOB_21 3
#define FMUX_GPIO_FUNCSEL_ADC3 0xf0
#define FMUX_GPIO_FUNCSEL_ADC2 0xf4
#define FMUX_GPIO_FUNCSEL_ADC3_MASK 7
#define FMUX_GPIO_FUNCSEL_ADC2_MASK 7
#define ADC3__IIC4_SCL 2
#define ADC2__IIC4_SDA 2
#define ADC3__XGPIOB_1 3
#define ADC2__XGPIOB_2 3
#define PINMUX_CONFIG(pad, function) mock_pinmux(#pad, #function)
void mock_pinmux(const char *, const char *);
''',
    'hal_dw_i2c.h': r'''
#include <stdint.h>
struct i2c_regs {
    uint32_t ic_con, ic_tar, p0[2], ic_cmd_data, ic_ss_scl_hcnt, ic_ss_scl_lcnt;
    uint32_t p1[5], ic_intr_mask, ic_raw_intr_stat, ic_rx_tl, ic_tx_tl, ic_clr_intr;
    uint32_t p2[4], ic_clr_tx_abrt, p3[5], ic_enable, ic_status, ic_txflr, ic_rxflr, ic_sda_hold;
    uint32_t ic_tx_abrt_source, p5[6], ic_enable_status;
};
#define IC_TX_ABRT 0x40
#define IC_STOP_DET 0x200
#define IC_ENABLE 1
#define IC_STATUS_MA 0x20
#define IC_STATUS_RFNE 8
#define IC_STATUS_TFE 4
#define IC_STATUS_TFNF 2
#define IC_CON_MM 1
#define IC_CON_SPD_SS 2
#define IC_CON_RE 0x20
#define IC_CON_SD 0x40
#define IC_CLK 100
#define IC_CMD 0x100
#define IC_STOP 0x200
''',
}

DRIVER_HARNESS = r'''
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "pg_profile.h"
#include "i2c4.h"
#include "hal_dw_i2c.h"
#define BASE 0x04040000u
#define MUX 0x03001000u
#define DDR 0x03021004u
static unsigned accesses, sleeps, enabled, stop, aborting, nack, stuck, disable_stuck;
static unsigned held_master, abort_stuck, abort_pending, abort_requests;
static unsigned abort_ignored, recovery_reads, recovery_nack, recovery_stuck, recovery_target;
static unsigned recovery_con, recovery_no_space;
static uint32_t ddr = 0xffffffff, mux_scl = 0x107, mux_sda = 0x207;
static uint32_t adc_scl = 0x402, adc_sda = 0x502, last_target;
/* DesignWare model: a received byte reaches the RX FIFO only once a later
 * command or its own STOP decides ACK/NACK; until then the master holds. */
static uint32_t commands[32], ncommands, rx_pending, rx_held, rx_value, tx_abort;
uint32_t mmio_read_32(uintptr_t address)
{
    accesses++;
    if (address == DDR) return ddr;
    if (address == MUX + 0x158) return mux_scl;
    if (address == MUX + 0x15c) return mux_sda;
    if (address == MUX + 0xf0) return adc_scl;
    if (address == MUX + 0xf4) return adc_sda;
    assert(address >= BASE && address < BASE + 0x100);
    switch (address - BASE) {
    case 0xfc: return 0x44570140;
    case 0x00: return recovery_con ? recovery_con : IC_CON_MM | IC_CON_RE | IC_CON_SD | IC_CON_SPD_SS;
    case 0x04: return recovery_target;
    case 0x9c: return (disable_stuck || held_master) ? 1 : enabled;
    case 0x6c:
        if (abort_pending && !abort_stuck) { if (!abort_ignored) held_master = 0; abort_pending = 0; }
        return enabled | (abort_pending ? 2 : 0);
    case 0x34: return aborting ? IC_TX_ABRT : stop ? IC_STOP_DET : 0;
    case 0x80: return tx_abort;
    case 0x54: aborting = 0; return 0;
    case 0x40: stop = aborting = 0; return 0;
    case 0x70: return stuck ? 0 : IC_STATUS_TFE | (recovery_no_space ? 0 : IC_STATUS_TFNF) |
        (held_master ? IC_STATUS_MA : 0) | (rx_pending ? IC_STATUS_RFNE : 0);
    case 0x74: case 0x78: return 0; /* FIFO levels, fault snapshot only */
    case 0x10: assert(rx_pending); rx_pending--; return rx_value++;
    default: assert(!"unexpected register read"); return 0;
    }
}
void mmio_write_32(uintptr_t address, uint32_t value)
{
    accesses++;
    if (address == DDR) { ddr = value; return; }
    if (address == MUX + 0x158) { assert(!(ddr & (1u << 20))); mux_scl = value; return; }
    if (address == MUX + 0x15c) { assert(!(ddr & (1u << 21))); mux_sda = value; return; }
    if (address == MUX + 0xf0) { assert(!(ddr & (1u << 1))); adc_scl = value; return; }
    if (address == MUX + 0xf4) { assert(!(ddr & (1u << 2))); adc_sda = value; return; }
    assert(address >= BASE && address < BASE + 0xa0); /* No motor/PWM/SPI/IRQ writes. */
    switch (address - BASE) {
    case 0x6c:
        if (value & 2) { assert(enabled); abort_requests++; abort_pending = 1; }
        enabled = value & 1; break;
    case 0x04:
        assert(value == 0x68 || (value == 0x69 && pg_profile_i2c4_route() == PG_I2C4_J3));
        last_target = value; break;
    case 0x10:
        if (held_master && abort_ignored) {
            assert(enabled && value == (IC_CMD | IC_STOP | 0x400));
            assert(recovery_target == 0x68 || (recovery_target == 0x69 && pg_profile_i2c4_route() == PG_I2C4_J3));
            recovery_reads++;
            if (recovery_stuck) return;
            held_master = 0; stop = 1;
            if (recovery_nack) { aborting = 1; tx_abort = 1; }
            return;
        }
        assert(ncommands < 32); commands[ncommands++] = value;
        if (nack) { aborting = 1; tx_abort = 1; break; }
        if (rx_held) { rx_held = 0; rx_pending++; }
        if (value & IC_CMD) { if (value & IC_STOP) rx_pending++; else rx_held = 1; }
        assert(rx_pending <= 2); /* never more than two bytes in flight */
        if (value & IC_STOP) stop = 1;
        break;
    case 0x00: assert(value == (1|2|0x20|0x40)); break;
    case 0x14: case 0x18: assert(value == 500); break;
    case 0x7c: assert(value == 30); break;
    case 0x30: case 0x38: case 0x3c: assert(!value); break;
    default: assert(!"unexpected register write");
    }
}
void mmio_clrsetbits_32(uintptr_t address, uint32_t clear, uint32_t set)
{
    mmio_write_32(address, (mmio_read_32(address) & ~clear) | set);
}
void arch_usleep(unsigned long us) { assert(us == 10 || us == 100); sleeps++; assert(sleeps <= 3000); }
void mock_pinmux(const char *pad, const char *function)
{
    accesses++;
    assert(!enabled && !held_master && !disable_stuck);
    if (pg_profile_i2c4_route() == PG_I2C4_CARRIER)
        assert((!strcmp(pad, "ADC3") && !strcmp(function, "IIC4_SCL")) ||
               (!strcmp(pad, "ADC2") && !strcmp(function, "IIC4_SDA")));
    else
        assert((!strcmp(pad, "VIVO_D1") && !strcmp(function, "IIC4_SCL")) ||
               (!strcmp(pad, "VIVO_D0") && !strcmp(function, "IIC4_SDA")));
}
int main(void)
{
    uint8_t bytes[16], payload[] = {0x98, 0x70};
    unsigned i, before;
    if (pg_profile_i2c4_route() == PG_I2C4_NONE) {
        assert(imu_i2c4_init() == IMU_I2C_DENIED);
        assert(imu_i2c4_read(NULL, 3, bytes, 16) == IMU_I2C_DENIED);
        assert(imu_i2c4_write(NULL, 0x20, payload, 2) == IMU_I2C_DENIED);
        assert(accesses == 0);
        return 0;
    }
    assert(!pg_profile_resource("i2c4"));
    assert(!pg_profile_i2c_address(4, 0x68));
    assert(pg_profile_i2c_address(4, 0x40));
    assert(pg_profile_i2c_address(4, 0x48));
    if (pg_profile_i2c4_route() == PG_I2C4_CARRIER) assert(pg_profile_i2c_address(4, 0x69));
    else assert(!pg_profile_i2c_address(4, 0x69));
    assert(pg_profile_i2c_address(3, 0x68));
    assert(pg_profile_gpio_output("B1", NULL));
    assert(pg_profile_gpio_output("B2", NULL));
    assert(pg_profile_gpio_output("B19", NULL));
    assert(pg_profile_gpio_output("B20", NULL) && pg_profile_gpio_output("B21", NULL));
    assert(pg_profile_gpio_output("A16", NULL) && pg_profile_gpio_output("A17", NULL));
    assert(pg_profile_resource("spi3") && pg_profile_resource("pwm0"));
    assert(imu_i2c4_init() == 0);
    if (pg_profile_i2c4_route() == PG_I2C4_CARRIER) {
        assert(ddr == (0xffffffffu & ~((1u << 20) | (1u << 21))));
        assert(mux_scl == 0x103 && mux_sda == 0x203);
    } else {
        assert(ddr == (0xffffffffu & ~((1u << 1) | (1u << 2))));
        assert(adc_scl == 0x403 && adc_sda == 0x503);
        assert(mux_scl == 0x107 && mux_sda == 0x207);
        assert(imu_i2c4_init_at(0x69) == 0);
        assert(imu_i2c4_read(NULL, 0, bytes, 1) == 0 && last_target == 0x69);
        assert(imu_i2c4_init() == 0);
        ncommands = rx_value = 0;
    }
    assert(imu_i2c4_read(NULL, 3, bytes, sizeof(bytes)) == 0);
    assert(ncommands == 17 && commands[0] == 3);
    assert(commands[1] == (IC_CMD | 0x400));
    for (i = 2; i < 16; i++) assert(commands[i] == IC_CMD);
    assert(commands[16] == (IC_CMD | IC_STOP));
    for (i = 0; i < 16; i++) assert(bytes[i] == i);
    assert(!enabled);
    before = accesses;
    assert(imu_i2c4_init_at(0x40) == IMU_I2C_DENIED && accesses == before);
    assert(imu_i2c4_init() == 0);
    ncommands = 0;
    assert(imu_i2c4_write(NULL, 0x20, payload, sizeof(payload)) == 0);
    assert(ncommands == 3 && commands[0] == 0x20);
    assert(commands[1] == 0x98 && commands[2] == (0x70 | IC_STOP));
    before = accesses;
    assert(imu_i2c4_read(NULL, 3, bytes, 17) == IMU_I2C_ARGUMENT);
    assert(imu_i2c4_read(NULL, 3, bytes, 0) == IMU_I2C_ARGUMENT);
    assert(imu_i2c4_write(NULL, 0x80, payload, 2) == IMU_I2C_ARGUMENT);
    assert(accesses == before);
    ncommands = 0; nack = 1;
    assert(imu_i2c4_read(NULL, 0, bytes, 4) == IMU_I2C_ABORT);
    assert(imu_i2c4_abort_source() == 1 && !enabled);
    nack = 0; ncommands = 0;
    assert(imu_i2c4_read(NULL, 0, bytes, 4) == 0);
    stuck = 1; ncommands = 0; sleeps = 0;
    assert(imu_i2c4_read(NULL, 0, bytes, 4) == IMU_I2C_TIMEOUT);
    assert(sleeps > 0 && sleeps <= 1000 && !enabled);
    stuck = 0;
    mux_scl = 6; mux_sda = 3; ddr = 0xffffffff;
    assert(imu_i2c4_init() == 0); /* Other pin functions must be preserved. */
    assert(ddr == 0xffffffff && mux_scl == 6 && mux_sda == 3);
    /* A running master can survive C906 reload with ENABLE already cleared.
     * Disabling alone cannot release it. Re-enable, then ABORT, before muxing. */
    held_master = 1; enabled = 0; sleeps = 0; before = abort_requests;
    assert(imu_i2c4_init() == 0);
    assert(abort_requests == before + 1 && !held_master && !enabled);
    assert(sleeps <= 2001);
    /* Actual robot-72 behavior: ABORT clears but master holds SCL.
     * A single read/RESTART/STOP at the existing allowed address releases it. */
    abort_ignored = held_master = 1; recovery_target = 0x68; sleeps = 0;
    assert(imu_i2c4_init() == 0 && recovery_reads == 1 && !held_master && !enabled);
    held_master = recovery_nack = 1; sleeps = 0;
    assert(imu_i2c4_init() == 0 && recovery_reads == 2 && !held_master && !enabled);
    recovery_nack = 0; held_master = 1; recovery_target = 0x40; sleeps = 0;
    assert(imu_i2c4_init() == IMU_I2C_TIMEOUT && recovery_reads == 2);
    recovery_target = 0x168; sleeps = 0; /* Never turn a ten-bit/special target into seven-bit. */
    assert(imu_i2c4_init() == IMU_I2C_TIMEOUT && recovery_reads == 2);
    recovery_target = 0x68; recovery_con = IC_CON_RE | IC_CON_SD; sleeps = 0;
    assert(imu_i2c4_init() == IMU_I2C_TIMEOUT && recovery_reads == 2); /* Not master. */
    recovery_con = IC_CON_MM | IC_CON_SD; sleeps = 0;
    assert(imu_i2c4_init() == IMU_I2C_TIMEOUT && recovery_reads == 2); /* No restart. */
    recovery_con = 0; recovery_no_space = 1; sleeps = 0;
    assert(imu_i2c4_init() == IMU_I2C_TIMEOUT && recovery_reads == 2);
    recovery_no_space = 0; recovery_stuck = 1; sleeps = 0;
    assert(imu_i2c4_init() == IMU_I2C_TIMEOUT && recovery_reads == 3);
    held_master = recovery_stuck = abort_ignored = 0;
    disable_stuck = 1; sleeps = 0; before = abort_requests;
    assert(imu_i2c4_init() == IMU_I2C_TIMEOUT);
    assert(sleeps > 0 && sleeps <= 1000 && abort_requests == before);
    disable_stuck = 0; held_master = abort_stuck = 1; sleeps = 0;
    assert(imu_i2c4_init() == IMU_I2C_TIMEOUT);
    assert(sleeps <= 2001 && held_master && abort_pending);
    { struct imu_i2c4_init_state state = imu_i2c4_last_init_state();
      before = accesses;
      assert(state.enable == 3 && state.enable_status == 1 && (state.status & IC_STATUS_MA));
      (void)imu_i2c4_last_init_state();
      assert(accesses == before); }
    return 0;
}
'''


if __name__ == '__main__':
    unittest.main()
