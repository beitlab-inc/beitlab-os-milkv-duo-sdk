# Hardware profiles

Select the connected hardware in your project's `platformio.ini`:

```ini
[platformio]
extra_configs = ../common/playground.ini
default_envs = my_app

[env:my_app]
extends = playground
custom_libs = beitlabduino
custom_profile = beitlab_robotics_rev011
```

Each shipped example explicitly selects its profile in its own `platformio.ini`.
The shared configuration keeps `beitlab_robotics_rev011` as a fallback for
projects that omit it. Use `custom_profile = duos_bare` only with the carrier
disconnected. Empty or unknown names fail the build. `platform` selects the existing C906 toolchain
integration, `board = duos-c906l` selects the processor, and `custom_profile`
selects the permissions for the attached hardware.

| Profile | Allowed GPIO outputs | Reserved connections |
| --- | --- | --- |
| `duos_bare` | A18, A19, A20, A28, A29 | Linux console, storage, networking and USB; every output not explicitly verified |
| `beitlab_robotics_rev011` | A29 (onboard blue LED) | All 24 pads in carrier map revision 0.11; SPI3, I2C4 and PWM controllers 0–2; Linux resources |

| Example folder | Explicit `custom_profile` | Purpose |
| --- | --- | --- |
| `hello` | `duos_bare` | Console output without GPIO |
| `mailbox_ping` | `duos_bare` | Linux/C906 mailbox ping |
| `heartbeat` | `duos_bare` | Optional header GPIO heartbeat; disabled by default |
| `blink` | `duos_bare` | LED heartbeat selected in `pin.h` |
| `beitlabduino_blink` | `duos_bare` | LED blink with setup()/loop() |
| `beitlabduino_mailbox` | `duos_bare` | LED control through mailbox commands |
| [`robotics_board`](../examples/platformio/robotics_board/README.md) | `beitlab_robotics_rev011` | Inspect carrier reservations and blink the onboard LED |

The nested `blink/heartbeat` copy also explicitly selects `duos_bare`.
Open `examples/platformio/robotics_board` for the carrier example. It
inspects B19, A18 and I2C4 reservations without accessing their hardware,
and initializes only A29. It stays idle if built with another profile.

The four bare-board header outputs use 3.3 V logic on J3. The pinned SDK's
U-Boot selects GPIO on those pads and A29; this image disables Linux's
touchscreen claims on A18/A19. Other header pads may be muxed to a bus,
connected to another subsystem, or use 1.8 V logic. They remain unavailable
until their ownership, mux and electrical connections are verified and
recorded in a manifest. These profiles apply to this image's device tree
and pinned boot firmware; they do not describe arbitrary Milk-V images.

Milk-V's [Duo S pinout](https://milkv.io/docs/duo/getting-started/duos#blue-led-pin)
lists A29 as the blue LED separately from J3/J4. The carrier generator wires
only the J3/J4 signals listed in its `J3MAP`/`J4MAP`; A29 is absent. The
starter firmware still touches no GPIO.

## Use from C

Existing beitlabduino programs keep the same API:

```c
#include "beitlabduino.h"

void setup(void)
{
    bl_print("profile %s", pg_profile_name());
    if (bl_pin_mode(BL_LED, BL_OUTPUT))
        bl_print("LED unavailable");
}

void loop(void)
{
    bl_write(BL_LED, BL_HIGH);
    bl_delay(500);
    bl_write(BL_LED, BL_LOW);
    bl_delay(500);
}
```

`bl_pin_mode()` returns -1 on refusal and leaves GPIO registers untouched.
For example, `bl_pin_mode("B19", BL_OUTPUT)` under the carrier profile prints:

```text
GPIO B19 refused [beitlab_robotics_rev011]: MOT_EN_REQ motor enable request; reserved for robot firmware
```

`"B019"` has the same identity and is also refused. Invalid, overflowing and
NULL pad names are rejected. Output initialization drives the pin low;
repeating initialization does not change its level. `bl_write()` only acts
on successfully initialized outputs and accepts equivalent pad spellings.

Programs using the FreeRTOS runtime directly can include `pg_gpio.h` and use
`pg_gpio_output(pad)` / `pg_gpio_write(pad, level)`, which return 0 or -1.
Heartbeat and its blink copies use this same driver. Heartbeat remains idle
until enabled in `pin.h`, and any enabled pin must pass the selected profile.

`pg_profile_gpio_output()` inspects permissions without touching hardware.
`pg_profile_resource()` explains peripheral reservations. There are currently
no application peripheral grants or PWM/SPI/I2C APIs; future drivers must
check the profile before changing controllers or pinmux.

## Maintain the profiles

`profiles/duos_bare.json` records the common board policy.
`profiles/beitlab_robotics_rev011.json` inherits it and replaces the robot
connections with reservations. It references `board_carrier.h` revision
0.11 and the carrier generator without requiring either sibling repository
to build an example. Add a new manifest for a different hardware revision.

An application pin entry needs `owner = application`,
`function = gpio_output`, `voltage`, `connector` and `purpose`. Record
schematic/pinout, SDK mux and Linux ownership evidence in the manifest's
`sources` before adding a grant. A child profile cannot release a Linux
pin. Unknown pins and resources are denied. Manifests contain pad identities,
not guessed register addresses; the driver uses the pinned SDK GPIO bases.

The builder generates the selected policy in its object directory and links
it into the ELF. It prints the profile name during a build; programs can
inspect it with `pg_profile_name()`. Direct builds also support:

```sh
python3 firmware/rtosbuild.py build --profile duos_bare --name my_app \
  --out /tmp/my_app.elf my_app.c
python3 firmware/profiles.py beitlab_robotics_rev011
python3 -m unittest discover -s scripts/tests -p test_profiles.py -v
```

The profile is selected during the build. This first version does not detect
the carrier, compare firmware metadata with a device profile, or reject
constant pin expressions at compilation. Selecting the bare profile on a
carrier can grant access to robot connections. Direct SDK/MMIO calls bypass
the checked API. Continue using the agent lock and the carrier's E-stop as
described in [FLASH.md](FLASH.md).

The GPIO driver preserves unrelated bits, but its FreeRTOS critical sections
cannot synchronize with Linux on the A53. The existing cross-core GPIO-port
register race remains; hardware verification is still required.
