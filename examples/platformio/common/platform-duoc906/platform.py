# SPDX-License-Identifier: MIT
from platformio.public import PlatformBase


class Duoc906Platform(PlatformBase):
    """No PlatformIO packages: the toolchain and FreeRTOS tree come from
    scripts/fetch-freertos.sh at the sdk.lock commit."""
