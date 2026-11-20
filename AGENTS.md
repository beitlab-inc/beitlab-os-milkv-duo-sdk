# beitlab Robotics MilkV duo OS

This repo is the flashable Linux image and the PC-side PlatformIO examples.
The robot firmware is in ../Balanced_robot. The carrier board is in ../Robotics_board.

Do not import those trees into the image. Do not vendor the Milk-V SDK.
Pin a commit in sdk.lock and fetch only the FreeRTOS tree the examples link.

The implementation contract is PROMPT.md. Follow it. Do not weaken the
remoteproc reload requirement into a full-image reboot on every upload.
