"""BMI160 byte protocol and beitlabduino J3 adapter, no physical hardware."""
from pathlib import Path
import shutil
import tempfile
import unittest
import test_robotics_imu as shared

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / 'examples/platformio/common/lib/beitlabduino_bmi160'


@unittest.skipUnless(shutil.which('cc'), 'needs a host C compiler')
class BMI160Test(unittest.TestCase):
    def test_byte_protocol_power_delays_samples_and_faults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(SOURCE / 'bmi160.h', root / 'bmi160.h')
            (root / 'test.c').write_text(MODEL + SENSOR_TEST)
            shared.IMUTest().compile_and_run(root, [SOURCE / 'bmi160.c'])

    def test_standalone_adapter_address_detection_policy_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('bmi160.h', 'beitlabduino_bmi160.h'):
                shutil.copyfile(SOURCE / name, root / name)
            (root / 'beitlabduino.h').write_text(
                '#include <stdint.h>\n#include "pg_profile.h"\n#define configTICK_RATE_HZ 200\nvoid bl_delay(uint32_t);\n'
                'void pg_print(const char *, ...);\n#define bl_print pg_print\n')
            (root / 'test.c').write_text(MODEL + ADAPTER_TEST)
            shared.IMUTest().compile_and_run(root, [SOURCE / 'bmi160.c', SOURCE / 'beitlabduino_bmi160.c'])


MODEL = r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include "bmi160.h"
static uint8_t regs[128];
static unsigned elapsed, writes, reads, last_reset, last_accel, last_gyro, pending_idle, probes;
static unsigned selected = 0x68, present = 0x68;
static int fail_bus, mismatch, force_error, force_suspend;

static int read_bus(void *ctx, uint8_t reg, uint8_t *bytes, size_t length)
{
    (void)ctx;
    reads++;
    assert(!pending_idle && reg + length <= sizeof(regs));
    if (!reg) { assert(length == 1); probes |= selected == 0x68 ? 1 : 2; }
    if (selected != present || fail_bus) return -1;
    if (!reg && writes) assert(elapsed >= last_reset + 10);
    if (reg == 0x03 && last_gyro) assert(elapsed >= last_gyro + 80);
    if (reg == 0x0c) assert(length == 12);
    if (reg == 0x20) assert(length == 2);
    memcpy(bytes, regs + reg, length); /* No dummy bytes. */
    if (reg == 0x02 && force_error) bytes[0] = force_error;
    if (reg == 0x03 && force_suspend) bytes[0] = 0;
    if (reg == 0x40 && mismatch) bytes[1] ^= 4;
    if (reg == 0x0c) regs[0x1b] &= ~0xc0;
    return 0;
}
static int write_bus(void *ctx, uint8_t reg, const uint8_t *bytes, size_t length)
{
    (void)ctx;
    assert(selected == present && !pending_idle);
    assert(length == 1); /* Suspend does not support burst writes. */
    if (fail_bus) return -1;
    writes++;
    pending_idle = 1;
    if (reg == 0x7e) {
        if (*bytes == 0xb6) {
            memset(regs, 0, sizeof(regs));
            regs[0] = 0xd1;
            last_reset = elapsed;
            last_accel = last_gyro = 0;
        } else if (*bytes == 0x11) {
            assert(elapsed >= last_reset + 10);
            regs[3] = (regs[3] & ~0x30) | 0x10;
            last_accel = elapsed;
        } else {
            assert(*bytes == 0x15 && elapsed >= last_accel + 4);
            regs[3] = (regs[3] & ~0x0c) | 4;
            last_gyro = elapsed;
        }
    } else {
        assert((reg >= 0x40 && reg <= 0x43) || (reg >= 0x50 && reg <= 0x53));
        if (reg >= 0x50) assert(!*bytes); /* Interrupts/output drivers off. */
        regs[reg] = *bytes;
    }
    return 0;
}
static void delay_bus(void *ctx, unsigned ms)
{
    (void)ctx;
    assert(ms >= 1);
    elapsed += ms;
    pending_idle = 0;
}
static void word(unsigned reg, int16_t value)
{
    regs[reg] = (uint16_t)value;
    regs[reg + 1] = (uint16_t)value >> 8;
}
static void sample_data(void)
{
    word(0x0c, 16384); word(0x0e, -16384); word(0x10, 123);
    word(0x12, 8192); word(0x14, -8192); word(0x16, INT16_MIN);
    word(0x20, 512);
    regs[0x1b] = 0xc0;
}
'''

SENSOR_TEST = r'''
int main(void)
{
    struct bmi160 imu = {.io = {NULL, read_bus, write_bus, delay_bus}};
    struct bmi160_sample sample, sentinel;
    unsigned before;
    memset(&sentinel, 0x55, sizeof(sentinel)); sample = sentinel;
    assert(bmi160_start(NULL) == BMI160_CONFIG_ERROR);
    assert(bmi160_read_sample(&imu, &sample) == BMI160_CONFIG_ERROR);
    regs[0] = 0x43;
    assert(bmi160_start(&imu) == BMI160_WRONG_ID && !writes);
    regs[0] = 0xd1; fail_bus = 1;
    assert(bmi160_start(&imu) == BMI160_BUS_ERROR && !writes);
    fail_bus = 0; mismatch = 1;
    assert(bmi160_start(&imu) == BMI160_CONFIG_ERROR && !imu.ready);
    mismatch = 0; force_error = 0x40;
    assert(bmi160_start(&imu) == BMI160_SENSOR_ERROR && !imu.ready);
    force_error = 0; force_suspend = 1;
    assert(bmi160_start(&imu) == BMI160_SENSOR_ERROR && !imu.ready);
    force_suspend = 0;
    assert(bmi160_start(&imu) == 0 && imu.ready);
    assert(regs[0x40] == 0x28 && regs[0x41] == 5 && regs[0x42] == 0x28 && !regs[0x43]);
    assert((regs[3] & 0x3c) == 0x14);
    assert(!regs[0x50] && !regs[0x51] && !regs[0x52] && !regs[0x53]);
    assert(bmi160_read_sample(&imu, &sample) == BMI160_NOT_READY);
    assert(!memcmp(&sample, &sentinel, sizeof(sample)));
    sample_data();
    before = writes;
    assert(bmi160_read_sample(&imu, &sample) == 0 && writes == before);
    assert(sample.gyro[0] == 16384 && sample.gyro[1] == -16384 && sample.gyro[2] == 123);
    assert(sample.accel[0] == 8192 && sample.accel[1] == -8192 && sample.accel[2] == INT16_MIN);
    assert(sample.temperature == 512);
    assert(bmi160_accel_mg(sample.accel[0]) == 1000 && bmi160_accel_mg(sample.accel[1]) == -1000);
    assert(bmi160_accel_mg(sample.accel[2]) == -4000);
    assert(bmi160_gyro_mdps(sample.gyro[0]) == 1000000 && bmi160_gyro_mdps(sample.gyro[1]) == -1000000);
    assert(bmi160_temperature_mc(sample.temperature) == 24000 && bmi160_temperature_mc(-512) == 22000);
    sample = sentinel;
    assert(bmi160_read_sample(&imu, &sample) == BMI160_NOT_READY);
    assert(!memcmp(&sample, &sentinel, sizeof(sample)));
    sample_data(); word(0x20, INT16_MIN);
    assert(bmi160_read_sample(&imu, &sample) == BMI160_NOT_READY);
    assert(!memcmp(&sample, &sentinel, sizeof(sample)));
    fail_bus = 1;
    assert(bmi160_read_sample(&imu, &sample) == BMI160_BUS_ERROR);
    assert(!memcmp(&sample, &sentinel, sizeof(sample)));
    fail_bus = 0; regs[3] = 0;
    assert(bmi160_read_sample(&imu, &sample) == BMI160_SENSOR_ERROR && !imu.ready);
    assert(!memcmp(&sample, &sentinel, sizeof(sample)));
    assert(bmi160_gyro_mdps(INT16_MIN) == -2000000);
    assert(bmi160_gyro_mdps(INT16_MAX) < 2000000);
    return 0;
}
'''

ADAPTER_TEST = r'''
#include <stdarg.h>
#include <stdio.h>
#include "pg_profile.h"
#include "beitlabduino_bmi160.h"
static enum pg_i2c4_route route = PG_I2C4_CARRIER;
static unsigned inits;
static int init_error;
static char message[256];
enum pg_i2c4_route pg_profile_i2c4_route(void) { return route; }
int imu_i2c4_init_at(unsigned address)
{
    assert(address == 0x68 || address == 0x69);
    inits++; selected = address; return init_error;
}
int imu_i2c4_read(void *ctx, uint8_t reg, uint8_t *bytes, size_t length) { return read_bus(ctx, reg, bytes, length); }
int imu_i2c4_write(void *ctx, uint8_t reg, const uint8_t *bytes, size_t length) { return write_bus(ctx, reg, bytes, length); }
uint32_t imu_i2c4_abort_source(void) { return 1; }
void bl_delay(uint32_t ms) { delay_bus(NULL, ms > 5 ? ms - 5 : 0); } /* Worst partial tick. */
void pg_print(const char *fmt, ...)
{
    va_list args; va_start(args, fmt); vsnprintf(message, sizeof(message), fmt, args); va_end(args);
}
int main(void)
{
    bl_bmi160_sample_t sample, sentinel;
    unsigned before;
    memset(&sentinel, 0x55, sizeof(sentinel)); sample = sentinel;
    assert(!bl_bmi160_address());
    assert(bl_bmi160_read(&sample) == BL_BMI160_ERROR && !reads);
    assert(bl_bmi160_begin() == BL_BMI160_ERROR && !inits && !reads && !writes);
    assert(strstr(message, "duos_bare_bmi160_j3"));
    route = PG_I2C4_J3; init_error = -2;
    assert(bl_bmi160_begin() == BL_BMI160_ERROR && !reads && !writes);
    init_error = 0; present = 0x69; regs[0] = 0xd1;
    assert(bl_bmi160_begin() == 0 && bl_bmi160_address() == 0x69 && probes == 3);
    assert(bl_bmi160_read(NULL) == BL_BMI160_ERROR);
    assert(bl_bmi160_read(&sample) == BL_BMI160_NOT_READY);
    assert(!memcmp(&sample, &sentinel, sizeof(sample)));
    sample_data();
    assert(bl_bmi160_read(&sample) == BL_BMI160_SAMPLE_READY);
    assert(sample.accel_mg[0] == 1000 && sample.accel_mg[1] == -1000 && sample.accel_mg[2] == -4000);
    assert(sample.gyro_mdps[0] == 1000000 && sample.gyro_mdps[1] == -1000000 && sample.temperature_mc == 24000);
    fail_bus = 1; sample = sentinel;
    assert(bl_bmi160_read(&sample) == BL_BMI160_ERROR && !bl_bmi160_address());
    assert(!memcmp(&sample, &sentinel, sizeof(sample)));
    fail_bus = 0; before = reads;
    assert(bl_bmi160_read(&sample) == BL_BMI160_ERROR && reads == before);
    assert(bl_bmi160_begin() == 0 && bl_bmi160_address() == 0x69);
    present = 0x68; regs[0] = 0x43; before = writes;
    assert(bl_bmi160_begin() == BL_BMI160_ERROR && writes == before);
    assert(strstr(message, "not found"));
    regs[0] = 0xd1;
    assert(bl_bmi160_begin() == 0 && bl_bmi160_address() == 0x68);
    regs[3] = 0;
    assert(bl_bmi160_read(&sample) == BL_BMI160_ERROR);
    assert(!memcmp(&sample, &sentinel, sizeof(sample)));
    return 0;
}
'''


if __name__ == '__main__':
    unittest.main()
