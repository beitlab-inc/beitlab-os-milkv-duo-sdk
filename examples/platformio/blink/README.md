# blink

What it does: toggles one GPIO at 1 Hz from the C906. The pin is set in `pin.h`.

This copy enables the onboard LED A29 in `pin.h` and blinks it at 1 Hz.

This example explicitly selects `custom_profile = duos_bare` for a Duo S without the carrier. Use an allowed output from [PROFILES.md](../../../docs/PROFILES.md), check your schematic, then edit `pin.h`.

Upload, from this folder: `pio run -t upload`

You should see `heartbeat idle`, or `heartbeat on <pad> ... 1 Hz`, in `pio run -t rtmonitor`.

A reserved or unverified pad prints `GPIO <pad> refused [<profile>]: <reason>` and is left untouched.
