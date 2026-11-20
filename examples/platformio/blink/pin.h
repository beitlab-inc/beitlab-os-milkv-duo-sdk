/* SPDX-License-Identifier: MIT */
#ifndef HEARTBEAT_PIN_H
#define HEARTBEAT_PIN_H

/*
 * The heartbeat pin.
 *
 * Rule: use a verified Duo S header output allowed by custom_profile.
 * This example selects duos_bare for a Duo S without the carrier.
 * Use the robotics_board example with beitlab_robotics_rev011 on the carrier.
 * Read docs/PROFILES.md and the schematic for your board revision first.
 * With PLAYGROUND_GPIO_ENABLE 0 this prints "heartbeat idle".
 * Pad names are a port letter and bit 0-31 (for example "A18").
 *
 * Limits, from the pinned SDK:
 *  - Ports A-D only. The SDK C906 GPIO driver (freertos/cvitek/driver/gpio)
 *    knows 0x03020000-0x03023000. Port E (PWR_GPIO, 0x05021000) is not
 *    supported here.
 *  - The pad must already be muxed to GPIO. This program does not touch
 *    pinmux.
 *  - Linux owns other pins on the same GPIO port. heartbeat changes only
 *    its own bit (read-modify-write). The C906 and the A53 still share the
 *    port registers.
 *
 * Example of an edited file, after checking the schematic:
 *   #define PLAYGROUND_GPIO_ENABLE 1
 *   #define PLAYGROUND_GPIO_PAD "X<n>"
 */
#define PLAYGROUND_GPIO_ENABLE 1
#define PLAYGROUND_GPIO_PAD "A29"

#endif
