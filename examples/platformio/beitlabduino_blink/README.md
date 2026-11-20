# beitlabduino_blink

For a Milk-V Duo S without a carrier: `platformio.ini` explicitly selects `custom_profile = duos_bare`.

Blinks the onboard LED A29 at 1 Hz using beitlabduino's `setup()`/`loop()` API.

Upload with `pio run -t upload`, then run `pio run -t rtmonitor` from this folder.

Expect `beitlabduino blink on A29`. Ctrl-C exits the monitor; the LED keeps blinking.

For the Beitlab carrier, use [robotics_board](../robotics_board/README.md). See [PROFILES.md](../../../docs/PROFILES.md) for allowed pins.
