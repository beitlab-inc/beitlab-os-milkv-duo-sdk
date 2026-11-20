# hello

Profile: `duos_bare`, explicitly selected in `platformio.ini` for a Duo S without the carrier.

What it does: a FreeRTOS task on the C906 prints `playground hello <build-id>` once per second. `<build-id>` is the compile date and time. It touches no GPIO.

Upload, from this folder: `pio run -t upload`

Watch the output: `pio run -t rtmonitor`, or the serial console on J22.

You should see `RTD: playground hello Nov 19 2026 16:42:07` once a second in the monitor. On serial the prefix is `RT:`. `pio run -t upload` exits with 0.

The SSH session and Linux keep running. Only the C906 restarts.

Another board: `PIO_DUO_HOST=192.168.1.50 pio run -t upload`
