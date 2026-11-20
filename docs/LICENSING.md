# OS name, upstream credits and license review

Review date: 2026-11-20. SDK revision:
`6f8962c394dd0a05729abb089f0feb7d5cc4aa5e` from
[Milk-V Duo Buildroot SDK V2](https://github.com/milkv-duo/duo-buildroot-sdk-v2/tree/6f8962c394dd0a05729abb089f0feb7d5cc4aa5e).

## Verified image identity

The existing `out/beitlab-robotics-milkv-duo-os-0.2.0-duos-arm64-sd.img`
was inspected directly by reading its MBR, extracting its third partition
into a temporary file and using read-only `debugfs` commands. Its identity is:

```ini
NAME="beitlab Robotics MilkV duo OS"
ID=beitlab-milkv-duo
ID_LIKE=buildroot
VERSION="0.2.0"
VERSION_ID="0.2.0"
PRETTY_NAME="beitlab Robotics MilkV duo OS 0.2.0"
```

The hostname is `beitlab-milkv-duo`. The console banner and release file use
the same beitlab name. SDK provenance remains in `/etc/duo-playground-release`.
Renaming the product does not change upstream ownership or licenses, and
this project does not claim to be an official Milk-V distribution.

## Findings

There is no top-level SDK license file at this pin. Its separate components
retain their individual licenses. A public GitHub repository alone does not
establish redistribution permission for every file it contains.

| Component | Evidence in the pinned SDK | Release implications |
| --- | --- | --- |
| Linux | `linux_5.10/COPYING`, `LICENSES/preferred/GPL-2.0`, `LICENSES/exceptions/Linux-syscall-note` | GPL-2.0-only kernel; preserve notices and satisfy corresponding-source obligations for binary distribution. The syscall exception does not relicense the whole image. |
| U-Boot | `u-boot-2021.10/Licenses/README`, `gpl-2.0.txt`, `Exceptions` | GPLv2, with per-file terms and documented exceptions; preserve notices and provide the required source and build material. |
| Buildroot and userspace | `buildroot/COPYING`, individual package recipes and license files | Buildroot's license is distinct from each installed package. Generate and review `legal-info` for the exact release configuration. Include the build system as appropriate to corresponding source. |
| FreeRTOS kernel | `freertos/License/license.txt`, `freertos/Source/LICENSE.md`, the initial comment in `freertos/Source/include/FreeRTOS.h` | MIT; preserve copyright notices and permission/disclaimer text. The license file contains a 2020 Amazon notice, and the actual kernel header contains a 2021 notice; both are retained. |
| Sophgo TDL SDK | `tdl_sdk/LICENSE` | BSD-2-Clause, copyright 2023 Sophgo Technologies. Binary redistribution must reproduce the notice, conditions and disclaimer in accompanying materials. This license is not a blanket grant for unrelated SDK directories. |
| FSBL libraries | Individual license files in `fsbl/lib/` | LZ4 has its own BSD notice, BigDigits has MPL-2.0 terms, and the LibTom libraries have their own notices. Determine which objects are linked into `fip.bin` before finalizing its manifest. |
| AIC8800 firmware and vendor code | `device/generic/rootfs_overlay/duos/mnt/system/firmware/aic8800`, `freertos/cvitek`, `cvi_mpi`, other SDK subtrees | No accompanying blanket redistribution grant was located for these entire subtrees. Obtain or identify applicable terms before redistribution; do not infer the firmware license from the Linux driver's module metadata. |

The shipped userspace includes, among others, BusyBox (GPL), glibc and Avahi
(LGPL and other component terms), OpenSSH (multiple permissive notices),
OpenSSL (Apache-2.0), dhcpcd (BSD), wpa_supplicant (BSD) and systemd (multiple
licenses). These are examples from the pinned Buildroot recipes, not a
complete bill of materials. Configuration and the actual linked objects
determine which obligations apply, including static linking in the agent.

Two concrete vendor-source gaps need follow-up: the linked
`freertos/cvitek/common/src/riscv64/snprintf.c` says "See LICENSE for license
details", but no corresponding license was found in that subtree; and the
mailbox source `freertos/cvitek/task/comm/src/riscv64/comm_main.c`, which the
local runtime follows, has no explicit license header. Several CVITEK
headers state copyright and "All rights reserved" without a license grant.
The FreeRTOS kernel's MIT license cannot simply be extended to those files.

## Credits and license material added

[THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES) acknowledges Milk-V,
Sophgo/CVITEK, Linux, U-Boot, Buildroot, Amazon/FreeRTOS, AIC Semiconductor,
RivieraWaves and userspace contributors.

The rootfs overlay stages this notice and selected unmodified upstream
license texts under `/usr/share/licenses/beitlab/`. The adjacent
`upstream-manifest.json` records each source path, pinned commit and SHA256.
The FreeRTOS kernel copyright header is also retained verbatim. This
collection preserves notices; it is not a complete license inventory or a
corresponding-source bundle. It does not assign a new license to upstream
code or change existing per-file SPDX identifiers.

**The already-built 0.2.0 image was not rewritten by this review.** These
new overlay notices must be included and checked in a subsequent build.
A 0.3.0 build was already running when this review began and had copied
its overlay; its eventual output must be inspected before claiming that
it contains the new notices. Distribute the repository notice alongside
an existing image in the meantime; that alone does not resolve the gaps
below.

## Work required before publishing a release

1. Once the image build has finished, generate Buildroot's legal material
   using the configuration that actually produced that release. Do not
   run this concurrently with an SDK build in the same output directory:

   ```sh
   sdk="${PLAYGROUND_SDK:-${BEITLAB_WORK_DIR:-$HOME/.cache/beitlab-duo-os}/sdk}"
   docker run --rm -v "$sdk:/home/sdk" \
     -e FORCE_UNSAFE_CONFIGURE=1 milkvtech/milkv-duo:latest \
     make -C /home/sdk/buildroot \
       O=/home/sdk/buildroot/output/milkv-duos-glibc-arm64-sd legal-info
   ```

   Review `legal-info/README`, manifests, licenses, sources, applied patches
   and all warnings. The current build configuration cannot retrospectively
   certify an older release whose configuration differs.
2. Inventory SDK-installed binaries, firmware and bootloader components
   that Buildroot's package mechanism does not cover. Resolve the specific
   vendor-source and firmware permission gaps above with their upstreams.
3. Preserve and supply the exact corresponding source required by applicable
   licenses, including local modifications, configurations, build and
   installation scripts, and required toolchain/runtime material. A credit
   line or link to upstream HEAD does not substitute for that material.
   Keep generated release source archives outside the tracked project;
   do not vendor the SDK or import the sibling robot/carrier trees.
4. Ship the notices and complete applicable license texts with the release;
   verify them inside the final image and include the associated legal and
   source artifacts in the release checksums. Track unresolved items explicitly.

No full `legal-info` collection or complete redistribution audit has been
completed by this review. It establishes the image name, preserves selected
notices, and identifies concrete remaining release work.

## Primary references

- [Linux kernel licensing rules](https://docs.kernel.org/process/license-rules.html)
- [Buildroot manual: legal notice and licensing](https://buildroot.org/downloads/manual/manual.html#legal-info)
- [Pinned Sophgo TDL SDK license](https://github.com/milkv-duo/duo-buildroot-sdk-v2/blob/6f8962c394dd0a05729abb089f0feb7d5cc4aa5e/tdl_sdk/LICENSE)

The subsequently built OS 0.4.0 includes the selected notices under `/usr/share/licenses/beitlab/`, verified in its final rootfs. The corresponding-source inventory and vendor permission gaps above remain open; notice inclusion alone is not a complete compliance delivery.
