# robotics_board

For the Milk-V Duo S on the Beitlab robotics carrier revision 0.11. Its `platformio.ini` explicitly selects `custom_profile = beitlab_robotics_rev011`.

The example prints the selected profile and the reservations for motor enable B19, knee servo A18, and I2C4, then blinks the onboard Duo S LED A29 at 1 Hz. The reservation probes only inspect policy; the example never initializes or writes a carrier pad. With a different profile it stays idle.

Power through the carrier and use Ethernet or configured Wi-Fi. Follow the [carrier lock/E-stop instructions](../../../docs/FLASH.md#7-on-the-carrier-lock-before-the-robot-can-move) before uploading: an upload replaces the currently running C906 program, including robot firmware. Keep the E-stop open while unlocking and running this example.

From this folder:

```sh
pio run -t upload
pio run -t rtmonitor
```

Expect `robotics board profile beitlab_robotics_rev011`, `B19 protected: ...`, `A18 protected: ...`, `I2C4 reserved: robot IMU/ADC/accessory bus`, and `robotics board heartbeat on A29`. Ctrl-C exits the monitor; the program keeps running.

See [PROFILES.md](../../../docs/PROFILES.md) for allowed pins and profile limitations. This example uses the playground runtime and beitlabduino; it does not compile the balance controller or motor drivers.
