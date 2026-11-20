> Historical `out/` and `sdk/` paths below describe earlier local builds. These generated directories were removed from the source checkout on 2026-11-20; current paths and GitHub build/release instructions are in [CI.md](CI.md).

# SDK notes

What was learned about the pinned Milk-V SDK while implementing PROMPT.md, with the command and the result. Paths are relative to `sdk/` unless they start with `image/`, `firmware/`, `agent/`, `examples/` or `scripts/`.

## Host used for this work

| Item | Value |
| --- | --- |
| Host | Ubuntu 24.04.5 LTS, x86_64, 16 cores, 62 GB RAM |
| SDK build host requirement | Ubuntu 22.04 amd64, or the `milkvtech/milkv-duo:latest` Docker image (SDK README, "Compiled using Docker") |
| Image build host (actual) | `scripts/build-image.sh --docker` on the host above: container `milkvtech/milkv-duo:latest`, which reports "Ubuntu 22.04.4 LTS x86_64" |
| What ran here | SDK inspection; patch application; DT compile; agent build and tests; the full image build (see [Build status](#build-status)) |
| What did not run here | anything on a Duo S: no board was on the bench |

This host is not 22.04, so the image was built with `scripts/build-image.sh --docker`.

## SDK pin

```
$ git ls-remote https://github.com/milkv-duo/duo-buildroot-sdk-v2 HEAD
6f8962c394dd0a05729abb089f0feb7d5cc4aa5e	HEAD
$ git log -1 --format='%H %ci %s'
6f8962c394dd0a05729abb089f0feb7d5cc4aa5e 2025-11-04 13:38:24 +0800 Merge branch 'develop'
$ git ls-remote https://github.com/milkv-duo/host-tools.git HEAD
67688c7335e7239f7592b86ed1432c97cbab245b	HEAD
```

These values are recorded in `sdk.lock`, together with these file hashes at that commit:

```
6d89ecf2613f0f6224a8ebd2bc8af7c992d40928da2b528ef95b7c4729031c48  freertos/cvitek/scripts/toolchain-riscv64-elf.cmake
0c161ca04037a9905f7655e073f593526fec22d5111e8b7a821fa18ebadac9e2  freertos/cvitek/driver/rtos_cmdqu/include/rtos_cmdqu.h
```

### Board defconfig

```
$ grep -n "FREERTOS\|RTOS_DUMP" build/boards/cv181x/sg2000_milkv_duos_glibc_arm64_sd/sg2000_milkv_duos_glibc_arm64_sd_defconfig
18:CONFIG_ENABLE_FREERTOS=y
19:CONFIG_ENABLE_RTOS_DUMP_PRINT=y
```

Line 20 is `CONFIG_DUMP_PRINT_SZ_IDX=17`. `scripts/build-image.sh` fails if either of the first two lines is missing.

### Boot partition layout

`device/milkv-duos-glibc-arm64-sd/genimage.cfg` defines:

- `boot.vfat`, labelled `boot`, containing `fip.bin` and `rawimages/boot.sd` (128M),
- a 2M `logo` partition,
- `rootfs.ext4` (768M).

The playground keeps this layout unchanged.

## Memory map

Source: `build/boards/cv181x/sg2000_milkv_duos_glibc_arm64_sd/memmap.py`.

```python
DRAM_BASE = 0x80000000
DRAM_SIZE = 512 * SIZE_1M
FREERTOS_SIZE = 2 * SIZE_1M
FREERTOS_ADDR = DRAM_BASE + DRAM_SIZE - FREERTOS_SIZE
FSBL_C906L_START_ADDR = FREERTOS_ADDR
KERNEL_MEMORY_SIZE = DRAM_SIZE - FREERTOS_SIZE
```

The generated output (`python3 build/scripts/mmap_conv.py --type ld <memmap.py> cvi_board_memmap.ld`) contains:

```
CVIMMAP_FREERTOS_ADDR = 0x9fe00000;
CVIMMAP_FREERTOS_SIZE = 0x200000;
CVIMMAP_FSBL_C906L_START_ADDR = 0x9fe00000;
CVIMMAP_KERNEL_MEMORY_ADDR = 0x80000000;
CVIMMAP_KERNEL_MEMORY_SIZE = 0x1fe00000;
```

`build/boards/default/dts/cv181x/cv181x_default_memmap.dtsi` gives Linux `memory@80000000` with `CVIMMAP_KERNEL_MEMORY_SIZE`. The C906 region at 0x9fe00000 therefore sits outside Linux RAM.

No address is written by hand anywhere in this repository:

- `firmware/rtosbuild.py` reads the values from the generated `cvi_board_memmap.ld`.
- `agent/Makefile` reads them from `cvi_board_memmap.h`.
- At run time, `rtos-agent` reads the carveout from `/proc/device-tree/reserved-memory/rtos_dram@*/reg`.

### Reset vector (FSBL)

`fsbl/plat/cv181x/platform.c`:

```c
void reset_c906l(uintptr_t reset_address)
	mmio_clrbits_32(0x3003024, 1 << 6);
	mmio_setbits_32(SEC_SYS_BASE + 0x04, 1 << 13);
	mmio_write_32(SEC_SYS_BASE + 0x20, reset_address);
	mmio_write_32(SEC_SYS_BASE + 0x24, reset_address >> 32);
	mmio_setbits_32(0x3003024, 1 << 6);
```

`fsbl/plat/cv181x/include/mmap.h` defines `#define BLCP_2ND_RUNADDR CVIMMAP_FSBL_C906L_START_ADDR`. So FSBL points the C906L reset vector at **0x9fe00000**.

## Remoteproc

### Search at the pinned commit: nothing

```
$ ls linux_5.10/drivers/remoteproc/
da8xx_remoteproc.c imx_rproc.c ingenic_rproc.c ... ti_k3_r5_remoteproc.c wkup_m3_rproc.c   (no cvitek/c906 driver)
$ grep -rn -i "remoteproc\|rproc" linux_5.10/arch/arm64/boot/dts/cvitek/ build/boards/cv181x/   (no output)
$ grep -n "REMOTEPROC\|RPMSG" build/boards/cv181x/sg2000_milkv_duos_glibc_arm64_sd/linux/*defconfig   (no output)
$ for r in origin/develop origin/sophgo origin/buildroot-2024.02; do git ls-tree --name-only $r linux_5.10/drivers/remoteproc/; done
   (generic remoteproc core files only, on every branch)
```

The v2 SDK has no C906 remoteproc driver on any branch, and `remoteproc0` does not exist.

### Source of the driver: v1 SDK, `arduino` branch

```
$ git clone --filter=blob:none --no-checkout https://github.com/milkv-duo/duo-buildroot-sdk v1sdk
$ git log --all --oneline -i --grep='remoteproc\|rproc\|arduino'
7bb22e4b add remoteproc for duos emmc kernel
ce6499ac Merge pull request #75 from kinsamanka/remoteproc-fix
5c0bf3e4 automatically run arduino firmware at boot
7221d232 add remoteproc and burn support for arduino
$ git log --oneline origin/arduino -- linux_5.10/drivers/remoteproc/cvitek_remoteproc.c
06c23aef Allow loading of firmware without resource tables. ...
9656a539 allow processor enough time to reset
7221d232 add remoteproc and burn support for arduino
```

| Item | Value |
| --- | --- |
| Driver file | `linux_5.10/drivers/remoteproc/cvitek_remoteproc.c`, the `arduino` branch at `7bb22e4bb73be3c1f3fba00fb20a121d725db7ac` |
| Kconfig symbol | `CVITEK_REMOTEPROC` (depends on `ARCH_CVITEK`, which exists for arm64 in `arch/arm64/Kconfig.platforms:329`) |
| Compatible | `cvitek,cv18xx-c906l-rproc` |
| Patch | `image/patches/0001` |

What the driver does:

- **start**: clear bit 6 of `0x3003024`, wait 10 µs, set it again. This is the same reset bit FSBL uses. The driver does **not** program the reset vector, so the vector stays where FSBL put it.
- **stop**: clear bit 6.
- **parse_fw**: register every `memory-region` of the DT node as a carveout. `rproc_elf_load_rsc_table()` failing is only a warning (06c23aef).
- **load / sanity / boot address**: the generic `rproc_elf_*` helpers.

The DT node in 7221d232 (added to `cv181x_default_memmap.dtsi`) is:

```dts
c906l: rtos_dram@CVIMMAP_FREERTOS_ADDR {
	reg = <0x0 CVIMMAP_FREERTOS_ADDR 0x0 CVIMMAP_FREERTOS_SIZE>;
	no-map;
};
c906l_rproc: c906l_rproc@0 {
	compatible = "cvitek,cv18xx-c906l-rproc";
	memory-region = <&c906l>;
	core-name = "cv18xx-c906l";
	firmware-name = "cvirtos.elf";
	status = "okay";
};
```

The playground uses the same node with `firmware-name = "playground.elf"` (`image/dts/playground_duos.dtsi`).

### Firmware ABI

Derived from the driver and `linux_5.10/drivers/remoteproc/remoteproc_elf_loader.c`, and enforced by both `agent/elfcheck.c` and `firmware/rtosbuild.py`.

- **ELF header.** ELF64, little endian, `EM_RISCV` (243), `ET_EXEC`. The float ABI flag must be double (`-mabi=lp64d`). The loader itself accepts ELF32 too; the playground is stricter because the SDK flags are `-march=rv64imafdc -mabi=lp64d`.
- **Entry point.** `e_entry == CVIMMAP_FSBL_C906L_START_ADDR` (0x9fe00000). The driver never writes the vector.
- **Segments.** At least one `PT_LOAD`. Every `PT_LOAD` (`p_paddr .. +p_memsz`) must lie inside the carveout [0x9fe00000, 0xa0000000), with `filesz <= memsz`.
- **Sections.** A `.text` section with `SHF_EXECINSTR` containing the entry. `freertos/cvitek/scripts/cv181x_lscript.ld` places `KEEP(*(.vectors))` (`_start`, `arch/riscv64/src/start.S`) first in `.text` at `CVIMMAP_FREERTOS_ADDR`.
- **No `.resource_table`.** It is not required since 06c23aef, so `firmware/include/resource_table.h` does not exist. No playground program has a trace buffer or vdev.

The vendor CVIRTOS build already produces such an ELF: `task/CMakeLists.txt` builds `cvirtos.elf`, then runs objcopy on it to make `cvirtos.bin`.

### Rebase notes

```
$ git apply --check image/patches/0001-remoteproc-add-cvitek-c906l-driver.patch   # on 6f8962c3
(no output, exit 0)
$ for p in image/patches/*.patch; do git apply --check $p && git apply $p; done   # pristine 6f8962c3
applied 0001 ... applied 0007
```

- **0001** is 7221d232 + 9656a539 + 06c23aef, limited to the driver, Kconfig, Makefile and `of_reserved_mem.{c,h}`. It applies with no fuzz. The RISC-V hunks of 7221d232 (moving `early_init_fdt_scan_reserved_mem()` in `arch/riscv`) are dropped: arm64 already calls it from `arch/arm64/mm/init.c:377`.
- **Why the `of_reserved_mem` hunk is needed.** At the pinned commit, `fdt_reserved_mem_save_node()` does `strncpy(rmem->name, uname, 8)` into `char name[8]`, unconditionally. `"rtos_dram@0x9fe00000"` would be stored without a terminator, and `of_reserved_mem_lookup()` (a `strcmp` against `kbasename(full_name)`) could not find it.
- **0002** (local) changes the guard to `!IS_ENABLED(CONFIG_CVITEK_REMOTEPROC)`. With `=m` the kernel defines only `CONFIG_CVITEK_REMOTEPROC_MODULE`, and the 8-byte copy would come back.
- **0007** (local) holds the C906L in reset at probe. See "Boot path".
- **Region count.** The reserved regions are now `ion`, `cvifb` and `rtos_dram`: 3 of `MAX_RESERVED_REGIONS` 4.
- **Regions outside RAM.** `__reserved_mem_reserve_reg()` (`drivers/of/fdt.c:476`) saves the node even when the region is outside `memory@`. The driver maps it with `ioremap_wc`, which arm64 allows for non-RAM.

## Boot path (decision)

**Choice: the kernel autostarts `playground.elf`.** `fip.bin` is not changed.

1. **Power-on.** FSBL loads the SDK's own `cvirtos.bin` from `fip.bin` (BLCP_2ND) and releases the C906L. This is the stock SDK FreeRTOS program, not the starter.
2. **Module load.** `playground-modules.service` insmods, from `/mnt/system/ko`:
   - `cv181x_sys`, `cv181x_base`, `cv181x_rtos_cmdqu`, `cv181x_fast_image` and `cv181x_clock_cooling`, in the stock `loadsystemko.sh` order. `fast_image` does its `LINUX_INIT_DONE` handshake with the running program.
   - Then `cvitek_remoteproc.ko`, last.
3. **Probe.** 0007 holds the C906L in reset. `rproc_add()` then auto-boots `firmware-name` (`rproc_alloc()` sets `auto_boot = true`, `remoteproc_core.c:2179`): it loads `/lib/firmware/playground.elf` and pulses reset. `remoteproc0` reads `running`.
4. **Agent.** `rtos-agent.service` (`rtos-agent boot`) writes the lock default and checks `remoteproc0`. It waits up to 20 s for `running`. If the core is still not running, it starts `playground.elf` again, then falls back to `playground-starter.elf`.

Why a module and not built in (`=y`, as on the arduino branch): a built-in driver probes before the root filesystem is mounted. Its auto-boot then finds no `/lib/firmware/playground.elf` and stays offline. The SDK copies in-tree modules flat into `/mnt/system/ko` (`build/Makefile` `copy_ko_action`) and nothing autoloads them, hence the explicit insmod.

After an upload, `/lib/firmware/playground.elf` is the uploaded program. The next power-on therefore runs the last uploaded program; `rtos-agent recover` returns to the starter. On a fresh card both files are the starter.

### Kernel rules that shape `rtos-agent load`

- `firmware` can only be written while the state is `offline` (`remoteproc_sysfs.c:166`, "can't change firmware while running").
- `stop` returns `EINVAL` unless the state is `running`, and `start` returns `EBUSY` while `running`.

So the agent writes `stop` first if the core is running, then `firmware`, then `start`. PROMPT.md lists firmware before stop/start. The order changes because the kernel refuses the other order; the effect is the same.

## Mailbox (`/dev/cvi-rtos-cmdqu`)

`freertos/cvitek/driver/rtos_cmdqu/include/rtos_cmdqu.h`:

```c
enum IP_TYPE { IP_ISP = 0, IP_VCODEC, IP_VIP, IP_VI, IP_RGN, IP_AUDIO, IP_SYSTEM, IP_CAMERA, IP_LIMIT, };
enum SYS_CMD_ID { SYS_CMD_INFO_TRANS = 0x50, ... SYS_CMD_INFO_TRACE_STREAM_STOP, SYS_CMD_INFO_LIMIT, };  // 0x50..0x5f, limit 0x60
struct cmdqu_t {
	unsigned char ip_id;
	unsigned char cmd_id : 7;
	unsigned char block : 1;
	union resv_t resv;          // valid {linux_valid, rtos_valid} or mstime
	unsigned int  param_ptr;
} __attribute__((packed)) __attribute__((aligned(0x8)));
#define RTOS_CMDQU_SEND_WAIT _IOW('r', CMDQU_SEND_WAIT, unsigned long)   // CMDQU_SEND_WAIT = 4
```

**Chosen id: `PLAYGROUND_CMD_PING = 0x40` on `IP_SYSTEM` (6)**, recorded in `firmware/include/playground_cmd.h`. It is valid because:

- `cmd_id` is 7 bits;
- the vendor `IP_SYSTEM` ids are 0x50–0x5f;
- `osdrv/interdrv/rtos_cmdqu/rtos_cmdqu.c` `rtos_cmdqu_send()` rejects `cmd_id >= SYS_CMD_INFO_LIMIT` ("invalid-id");
- `task/comm/src/riscv64/comm_main.c` has no `IP_SYSTEM` case at 0x40.

### Linux driver behaviour the host side depends on

- `RTOS_CMDQU_SEND_WAIT` sets `block = 1`, sends, and waits `resv.mstime` ms for a reply with the same `ip_id`/`cmd_id`, `rtos_valid == 1` and `block == 1`. It **returns 0 even on timeout**, and then copies the request back unchanged. `host_ping` therefore judges by content: a real reply carries `valid = {1,1}`, which is why `mstime == 257` is refused, and `param_ptr == nonce + 1`.
- **Vendor bug (patched, 0005).** `cvi_rtos_cmdqu_ioctl()` calls `kfree(wait_list)` on an uninitialized local in the `SEND_WAIT` case. 0005 initializes it to NULL.

### C906 side

The vendor comm task:

- routes by `ip_id` in `prvQueueISR`;
- echoes unknown `IP_SYSTEM` commands back unchanged (`default: goto send_label`);
- replies to `SEND_TO_CPU0` (A53) or `SEND_TO_CPU1` (big C906) depending on `transfer_config.conf_magic`.

`transfer_config` is copied from mailbox RAM 0x1900400, which FSBL fills (`driver/common/src/system.c` `GetCommInfo()`). After a remoteproc reload that block has been cleared. The checksum then fails and the defaults apply, with `conf_magic = RTOS_MAGIC_HEADER`, which is **`C906_MAGIC_HEADER` when compiled for `__riscv`** (`fast_image.h`). Unpatched, a reloaded program would send its replies to the wrong CPU.

`firmware/runtime/playground_rt.c` therefore:

- always replies to `SEND_TO_CPU0` and resets `conf_magic` to `CA53_MAGIC_HEADER`;
- handles `LINUX_INIT_DONE`, `DUMP_MSG`, `DUMP_EN` and `DUMP_DIS` like the vendor task;
- hands playground ids to subscribed tasks and echoes the rest.

### Hardware spinlock (known risk)

Linux and the C906 share `SPIN_MBOX` (field 4, `MAILBOX_REG_BASE + 0xc0`). A holder writes its id and the lock is released by writing the same id again (`driver/spinlock/src/cvi_spinlock.c`). If the C906 is reset while it holds `SPIN_MBOX`, which takes microseconds per reply, the lock stays taken and Linux mailbox sends fail with `MAILBOX_LOCK_FAILED`. Nothing in the SDK clears it. Not mitigated; pending hardware.

## Log (`rtos-agent log`)

The driver has no remoteproc trace buffer (no resource table). The vendor dump ring is used instead:

- **C906 side.** `driver/uart/src/uart.c` `dump_uart_*` keeps a ring in the C906 heap. The size is `1 << transfer_config.dump_print_size_idx` (FSBL passes `CONFIG_DUMP_PRINT_SZ_IDX=17`; after a reload the default 4 KiB applies). Lines go there with an `RTD: ` prefix, and to UART0 with `RT: `.
- **Linux side.** `osdrv/interdrv/fast_image/fast_image.c` (`/dev/cvi-fast-image`) reads the ring:
  - `FAST_IMAGE_QUERY_DUMP_MSG_INFO` (`_IOW('r', 37, unsigned long)`) asks the C906 with `SYS_CMD_INFO_DUMP_MSG` and returns the 18-byte `struct dump_uart_s`;
  - `FAST_IMAGE_QUERY_DUMP_MSG` (`_IOW('r', 36, ...)`) returns the ring linearized.
  - Both are copied verbatim into `agent/vendor_abi.h`.
- **Playground side.** The runtime enables the ring in both boot paths. `pg_print()` writes each line to both UART and ring.
- **No answer.** If the program does not answer `DUMP_MSG`, or the device is missing, `rtos-agent log` says so. It names `/dev/ttyS0` (`console=ttyS0,115200` in `u-boot-2021.10/include/configs/cv181x-asic.h`), and FLASH.md treats the serial console as the log.
- **Follow mode.** `log -f` polls every 500 ms and prints only new bytes. It exits when its SSH parent goes away.

## C906 toolchain and FreeRTOS tree

- **Flags.** `firmware/rtosbuild.py` parses the flags out of `toolchain-riscv64-elf.cmake` rather than copying them:

  ```
  $ python3 -c "...parse_toolchain_cmake(...)"
  -O2 -std=gnu11 -march=rv64imafdc -mabi=lp64d -ffunction-sections -fdata-sections -Wl,--gc-sections
  -Wno-pointer-to-int-cast -fno-builtin -Wno-missing-field-initializers -g -Wall -Wextra -nostdlib
  -mcmodel=medany -DFREERTOS_BSP -DLINUX_BSP_128MB -D__riscv_xlen=64 -DTHEAD_C906 -DCONFIG_64BIT -DRISCV_FPU
  ```

  `task/CMakeLists.txt` adds `-Wall -Wextra -fno-strict-aliasing -static --specs=nosys.specs -DCVIRTOS`.

- **`DDR_64MB_SIZE` must be set.** The toolchain file contains `if ($ENV{DDR_64MB_SIZE} STREQUAL "y")`. With the variable unset, CMake stops:

  ```
  CMake Error at .../toolchain-riscv64-elf.cmake:42 (if): if given arguments: "STREQUAL" "y"  Unknown arguments specified
  ```

  The SDK exports it from `.config` (`build/scripts/boards_scan.py` `gen_build_env`, kconfiglib `str_value`). For this board `# CONFIG_DDR_64MB_SIZE is not set` gives `n`. `scripts/build-rtos-libs.sh` and `rtosbuild.py` both derive it the same way.

- **The T-Head toolchain is required.** The vendor sources use T-Head custom CSRs. With Ubuntu's `gcc-riscv64-unknown-elf` 13.2 plus picolibc (Docker `ubuntu:24.04`):

  ```
  freertos/cvitek/arch/riscv64/src/cache.c:59: Error: unknown CSR `mhcr'
  ```

  The SDK toolchain is `host-tools/gcc/riscv64-elf-x86_64` (T-Head). The SDK documents no standalone tarball, so `scripts/fetch-freertos.sh` takes that subtree of host-tools at `host_tools_commit` and records its path in `sdk/PLAYGROUND_TOOLCHAIN`. It is an x86_64 Linux binary; Windows users go through WSL2.

- **`freertos/cvitek` is not self-contained.**
  - `kernel/CMakeLists.txt` and `hal` compile `${TOP_DIR}/../Source` and `${TOP_DIR}/../Tracealyzer`.
  - `common` and `driver` read `${BUILD_ENV_PATH}/.config`.
  - The linker script `INCLUDE`s `cvi_board_memmap.ld`.

  So the sparse set `fetch-freertos.sh` takes is: `freertos/{cvitek,Source,Tracealyzer}` and `build/{Kconfig,scripts,boards,panels,sensors}`, about 17 MB. It generates `.config` and the memmap the way the SDK does:

  ```
  $ cd build && python3 scripts/boards_scan.py --gen-build-kconfig && python3 scripts/defconfig.py --kconfig Kconfig boards/cv181x/.../..._defconfig
  Configuration saved to '.config'
  CONFIG_CHIP_ARCH_cv181x=y  CONFIG_BOARD="milkv_duos_glibc_arm64_sd"  CONFIG_FAST_IMAGE_TYPE=0  CONFIG_ENABLE_FREERTOS=y
  ```

- **The SDK `FreeRTOS.h` does not include `<stdint.h>`.** Vendor sources include `<stdio.h>` first, and newlib provides the types through it. The playground sources include `<stdint.h>` first, which was found by the type-check below.

- **Libraries linked.** The CVIRTOS stages install `arch kernel common hal driver`. Driver and hal sub-libraries are CMake OBJECT libraries folded into `libdriver.a` and `libhal.a`. The task stage (`comm`, `rgn`, `audio`) is not linked, and the driver list (`common pinmux uart spinlock gpio rgn rtos_cmdqu fast_image`) references no task symbol.

- **Type-check of the playground C906 sources.** Run in Docker `ubuntu:24.04`, with Ubuntu riscv gcc + picolibc headers, against the vendor headers, using the exact parsed flags and `-fsyntax-only`:

  ```
  SYNTAX OK: firmware/runtime/playground_rt.c (1 warnings)   <- vendor DEFINE_CVI_SPINLOCK initializer; comm_main.c has the same
  SYNTAX OK: firmware/starter/starter.c (0 warnings)
  SYNTAX OK: examples/platformio/hello/src/main.c (0 warnings)
  SYNTAX OK: examples/platformio/mailbox_ping/src/main.c (0 warnings)
  SYNTAX OK: examples/platformio/heartbeat/src/main.c (0 warnings)
  OK: enabled+empty pad rejected: PLAYGROUND_GPIO_ENABLE is 1 but PLAYGROUND_GPIO_PAD is empty: see pin.h
  ```

  This is not a link. The real link with the T-Head toolchain is in [Build status](#build-status).

## Rootfs

- **systemd needs a newer toolchain.** In `buildroot/package/systemd/Config.in`, systemd `depends on BR2_TOOLCHAIN_GCC_AT_LEAST_8`. The SDK defconfig uses the external Linaro 7.3.1 (`BR2_TOOLCHAIN_EXTERNAL_GCC_7`). Patch 0006 switches the **rootfs** toolchain to `BR2_TOOLCHAIN_EXTERNAL_BOOTLIN_AARCH64_GLIBC_STABLE` (2024.05-1: gcc 13, headers 4.19, which is ≤ the 5.10 kernel). The kernel, u-boot, FSBL, osdrv and middleware still build with host-tools as before. Their glibc binaries run on the newer glibc. Pending hardware.
- **Package changes (0006).** OpenSSH replaces Dropbear and gesftpserver (OpenSSH installs `/usr/libexec/sftp-server`). The following are also changed:
  - `avahi-daemon` added;
  - hostname `duo-playground`, root password `playground`;
  - Python 3 and pip removed (PROMPT.md: no Python on the device);
  - dnsmasq removed: under systemd it would serve DHCP on the Ethernet LAN, whereas the stock image only ran it for USB RNDIS;
  - networkd and resolved off; dhcpcd from the stock image stays.
- **Unit enablement.** Buildroot runs `systemctl preset-all` on a copy of the target while it creates the rootfs images (`SYSTEMD_ROOTFS_PRE_CMD_HOOKS`). The overlay's `50-playground.preset` enables the playground units. `build-image.sh` checks the enablement in `rootfs.tar.xz`, not in `output/target`.
- **Stock init scripts.** Buildroot builds systemd with `-Dsysvinit-path=` (`systemd.mk:96`), so the SDK's `/etc/init.d/S99user` (which loads every mpp module, `cv181x_pwm.ko` and `blink.sh`) does not run. `playground-modules.service` loads only the modules listed in "Boot path".
- **Overlay injection.** `build/Makefile` `br-rootfs-prepare` copies `device/$(MV_BOARD)/overlay/*` last, so `scripts/build-image.sh` installs `image/overlay` plus the generated files there.

## Carrier device tree overlay

`../Balanced_robot/include/balance/board_carrier.h` (rev 0.11) gives the C906:

- SPI3 (B13 B14 B15 B16 plus GPIO chip selects B18 B21 B22);
- I2C4 (B1 B2);
- PWM 1-3 / 6-7 / 8-10, which are controllers 0, 1 and 2 on pads B11 B12 B20 A18 A19 E0 E1 E2;
- GPIOs A20 A28 B3 B19 C18 C19 C20.

What the board DT claims at the pinned commit (`dts_arm64/sg2000_milkv_duos_glibc_arm64_sd.dts` plus includes):

| Node | Claim | Carrier pad? |
| --- | --- | --- |
| `&spi3` `status = "okay"` with `spidev@0` | SPI3 controller | yes |
| `&i2c4` `status = "okay"` with `gt9xx@14`, `goodix,irq-gpio = <&porta 18>`, `goodix,rst-gpio = <&porta 19>` | I2C4, A18, A19 | yes |
| `pwm0..pwm3` (no status, so enabled) | PWM controllers | 0-2 yes |
| `&vo`, `&mipi_tx`: `pwm-gpio <&porte 0>`, `power-ct-gpio <&porte 1>`, `reset-gpio <&porte 2>` | E0 E1 E2 | yes |
| `wifi_pin` `poweron-gpio <&porta 15>`, `wakeup-gpio <&porte 7>`; `bt_pin` `<&porte 9>` | A15 E7 E9 | no |
| `cv-sd` `cvi-cd-gpios <&porta 13>`; `usb` `vbus-gpio <&portb 6>` | A13 B6 | no |

Pad names map to XGPIO ports by letter and bit. U-Boot `cvi_board_init.c` muxes `JTAG_CPU_TCK` to `XGPIOA_18`, which `board_carrier.h` calls A18. The stock `duo-init.sh` names `gpio_b17`. The E pads are on `porte` (`gpio@05021000`, PWR_GPIO).

The overlay, `image/dts/playground_duos.dtsi`, included by 0004:

```dts
/ {
	reserved-memory {
		#size-cells = <0x2>; #address-cells = <0x2>; ranges;
		c906l: rtos_dram@CVIMMAP_FREERTOS_ADDR {
			reg = <0x0 CVIMMAP_FREERTOS_ADDR 0x0 CVIMMAP_FREERTOS_SIZE>;
			no-map;
		};
	};
	c906l_rproc: c906l_rproc@0 {
		compatible = "cvitek,cv18xx-c906l-rproc";
		memory-region = <&c906l>;
		core-name = "cv18xx-c906l";
		firmware-name = "playground.elf";
		status = "okay";
	};
};
&spi3 { status = "disabled"; };
&i2c4 { status = "disabled"; };
&pwm0 { status = "disabled"; };
&pwm1 { status = "disabled"; };
&pwm2 { status = "disabled"; };
&vo { status = "disabled"; };
&mipi_tx { status = "disabled"; };
```

Linux keeps ethernet0, usb, sd, uart0, `rtos_cmdqu`, `fast_image`, the GPIO controllers and pwm3. U-Boot still muxes SPI3 and I2C4 pads to those functions (`cvi_board_init.c`), which is what the carrier wants. Linux just does not drive them.

## Hardware profiles (2026-11-20)

The source now selects a GPIO policy with `custom_profile` in PlatformIO
or `rtosbuild.py build --profile`. The default is
`beitlab_robotics_rev011`; `duos_bare` requires explicit selection.
See [PROFILES.md](PROFILES.md) for the API and limits. These changes have
not been installed on a board or included in a rebuilt SD image.

Evidence checked for the initial output grants:

```sh
cat sdk/build/boards/cv181x/sg2000_milkv_duos_glibc_arm64_sd/u-boot/cvi_board_init.c
```

At `sdk.lock`'s commit, U-Boot selects `XGPIOA_18`, `XGPIOA_19`,
`XGPIOA_20`, `XGPIOA_28` and `XGPIOA_29`. The board DTS disables I2C0
(A28/A29), and this image disables the I2C4 touchscreen and PWM1 claims
on A18/A19. Only those five outputs are granted on a bare board. All four
header pins are reserved by the carrier profile.

The [official Duo S pinout](https://milkv.io/docs/duo/getting-started/duos#blue-led-pin)
lists A29 as the onboard blue LED separately from J3/J4; the carrier
generator's `J3MAP`/`J4MAP` do not connect it. This is the evidence for
retaining A29 in the example profiles. The starter remains GPIO-free.

The carrier manifest was compared with the pad names in
`../Balanced_robot/include/balance/board_carrier.h`, revision 0.11:
all 24 pads match, including the PWM-comment pads and B1/B2 from the
I2C4 comment. Sibling repositories are inspection references only and
are not required by the example builder.

Verification:

| Command/check | Result |
| --- | --- |
| `python3 -m unittest discover -s scripts/tests -p test_profiles.py -v` | 4 tests pass; real C policy/GPIO code compiled with AddressSanitizer and UndefinedBehaviorSanitizer for both profiles; all 24 carrier pads and zero-prefixed aliases refused without register access |
| `pio run` in hello, mailbox_ping, heartbeat, blink, beitlabduino_blink, beitlabduino_mailbox | All six build with the default carrier profile and pass the existing ELF ABI check |
| `firmware/starter/build.sh firmware/build/profile-starter.elf` | Builds and passes the ELF ABI check; does not toggle GPIO |
| Temporary PlatformIO project with `custom_profile = duos_bare` | Build succeeds and reports the bare profile |
| Same project with `custom_profile = unknown_profile` | Build fails with `cannot load hardware profile` |
| GPIO behavior on hardware | Pending; host tests use instrumented register stubs |

SDK `task/main/src/main.c` still emits the existing implicit-declaration
warnings, and `playground_rt.c` emits the existing spinlock initializer
warning. The new policy and GPIO code passes host `-Wall -Wextra -Werror`.

Each example now explicitly selects its hardware in its own `platformio.ini`.
The six existing top-level examples and nested `blink/heartbeat` select
`duos_bare`. The new `robotics_board` example selects
`beitlab_robotics_rev011`, inspects B19/A18/I2C4 reservations without
accessing their hardware, and blinks only the onboard LED A29. It stays
idle if built with another profile. The shared fallback remains the
carrier profile. `pio run` passes for all eight projects and reports the
expected profile for each; the four profile host tests also pass. Carrier
execution remains pending. The nested copy's relative platform/upload/monitor
paths were corrected, and the builder locates the repo from the resolved
platform directory so nested examples build without `PLAYGROUND_REPO`.

## Other findings

- **Duo S LED.** U-Boot muxes `IIC0_SDA` to `XGPIOA_29` under the comment "LED". No schematic in this repository shows that pad is free of the carrier sockets, so the starter toggles nothing.
- **Vendor GPIO driver.** `driver/gpio/src/gpio.c` `gpio_direction_output()` writes the whole port's OE and DR registers (`1 << pin`, `val << pin`). It would clear every other output on that port, including Linux's, and it only knows ports A–D. `heartbeat` does read-modify-write of its own bit instead. Linux and the C906 still share the port registers with no lock; that is a known race, pending hardware.
- **Monitor target name.** PlatformIO reserves `-t monitor`: `platformio/run/cli.py` runs its serial device monitor for it. The network log is therefore `pio run -t rtmonitor`, configured as `custom_monitor = python3 ../common/monitor.py`. PROMPT.md's `monitor = ...` became `custom_monitor`, because PlatformIO only allows custom options with the `custom_` prefix.
- **Upload cwd.** `upload_command = python3 ../common/upload.py $SOURCE` works because PlatformIO runs SCons with the project directory as cwd (`run/cli.py` `with fs.cd(project_dir)`).

## Verification done here

| What | Result |
| --- | --- |
| `git apply` of `image/patches/0001..0007` on a pristine 6f8962c3 tree | all apply, in order, no fuzz |
| DT of the patched board, cpp + DTC 1.7.0 (Docker), then `scripts/check-dtb.py --carrier board_carrier.h --base 0x9fe00000 --size 0x200000` | `RESULT: PASS` (report below) |
| The same on the **stock** DT (negative test) | `RESULT: FAIL`: no rproc node; spi3/i2c4/pwm0-2/vo/mipi_tx enabled; A18 A19 E0 E1 E2 claimed |
| `make -C agent test` (host build, fake sysfs) | unit: 14 checks pass; CLI: all checks pass (`PASSED: test_cli`) |
| `make -C agent` production build | static, 0 warnings (host gcc; the image uses the aarch64 host-tools gcc) |
| `-fsyntax-only` of all C906 sources against the vendor headers | pass (see above) |
| Full image build, `scripts/build-image.sh --docker` | exit 0; all post-build checks pass (see [Build status](#build-status)) |
| Starter ELF linked with the SDK T-Head toolchain | entry 0x9fe00000, one LOAD in the carveout, lp64d; passes the ABI check |
| `pio run` in `examples/platformio/hello` (PlatformIO 6.1.19, host gcc) | the platform installs and the builder runs `rtosbuild`. `build-rtos-libs.sh` configures the vendor CMake and fails at the first vendor compile for lack of libc headers. Expected without the SDK toolchain. |
| `upload.py` against fake `ssh`/`scp` | ssh `true`, then scp to `/run/playground-upload.elf`, then `rtos-agent load` with remove-after. Agent exit 3 → exit 3 plus the unlock hint; exit 0 → 0 |

Patched DT check report (from the locally compiled dtb, sha256 `8bdb896a4fbaded9…`):

```
ok:   rproc node /c906l_rproc@0 enabled
ok:   firmware-name = "playground.elf"
ok:   carveout /reserved-memory/rtos_dram@0x9fe00000 is no-map
ok:   carveout reg 0x9fe00000+0x200000 matches memmap 0x9fe00000+0x200000
ok:   spi3 /spi3@041B0000 disabled
ok:   i2c4 /i2c@04040000 disabled
ok:   pwm0 /pwm@3060000 disabled
ok:   pwm1 /pwm@3061000 disabled
ok:   pwm2 /pwm@3062000 disabled
ok:   vo /vo disabled
ok:   mipi_tx /mipi_tx disabled
ok:   ethernet enabled
ok:   sd enabled
ok:   uart0 enabled
ok:   rtos_cmdqu enabled
GPIO references in enabled nodes:
      A13  /cv-sd@4310000 cvi-cd-gpios
      A15  /wifi_pin poweron-gpio
      B6   /usb@04340000 vbus-gpio
      E7   /wifi_pin wakeup-gpio
      E9   /bt_pin poweron-gpio
carrier pads (../Balanced_robot/include/balance/board_carrier.h): A18 A19 A20 A28 B1 B11 B12 B13 B14 B15 B16 B18 B19 B2 B20 B21 B22 B3 C18 C19 C20 E0 E1 E2
ok:   no enabled node claims a carrier pad
RESULT: PASS
```

## Build status

**Built.** `scripts/build-image.sh --docker` completed on 2026-11-20 (exit 0). The SDK `build.sh` stage took about 25 minutes with downloads cached.

```
$ cat out/SHA256SUMS
f16a662e3efb2e4895c7db0810b73adf362fb34dd952ff9d1af0a5a197ab4074  duo-playground-0.1.0-duos-arm64-sd.img
597d937c7af136b00014c4366636693bead24f7b3282cd96745a73afb6d31292  rtos-agent-0.1.0-aarch64
$ cat out/build-checks.txt
ok: .../linux_5.10/build/sg2000_milkv_duos_glibc_arm64_sd/.config has CONFIG_REMOTEPROC=y CONFIG_CVITEK_REMOTEPROC=m CONFIG_CGROUPS=y
ok: .../arch/arm64/boot/dts/cvitek/sg2000_milkv_duos_glibc_arm64_sd.dtb passes scripts/check-dtb.py (out/dtb-check.txt), sha256 8bdb896a4fbaded9
ok: rootfs has the remoteproc module, agent, firmware, sshd, avahi; rtos-agent.service enabled
ok: /home/work/out/duo-playground-0.1.0-duos-arm64-sd.img
```

The kernel-built dtb has the same sha256 as the dtb compiled earlier from the same patched sources. The check-dtb report above therefore describes the shipped dtb byte for byte.

### Image inspection

```
$ sfdisk -l out/duo-playground-0.1.0-duos-arm64-sd.img
...img1 *      1  262144  262144  128M  c W95 FAT32 (LBA)
...img2   262145  266240    4096    2M  0 Empty           (logo)
...img3   266241 1839104 1572864  768M 83 Linux
$ minfo/mdir -i img@@512 ::        (in the SDK container)
disk label="boot"
fip      bin    384000
boot     sd    3506048
```

genimage copies `rawimages/boot.sd` to the root of the FAT under its basename, as the SDK `genimage.cfg` defines.

What `rootfs.tar.xz` contains (`tar -xJOf`):

- **`/etc/duo-playground-release`**: `DUO_PLAYGROUND_VERSION=0.1.0`, `SDK_COMMIT=6f8962c394dd0a05729abb089f0feb7d5cc4aa5e` (= `sdk.lock`), `STARTER_FIRMWARE=nogit-b8b8fcf01c77`. This repository has no git history yet, so the starter id is a hash of its sources.
- **`/etc/issue`**: the release line, `Network name: duo-playground.local`, `SSH login: root  password: playground`, `Change the password with: passwd`.
- **Identity**: `/etc/hostname` is `duo-playground`, `/etc/rtos-agent/lock` is `unlocked`, and `sshd_config` has `PermitRootLogin yes`, `PasswordAuthentication yes` and the `sftp-server` subsystem.
- **Enabled units**: `rtos-agent`, `playground-keys`, `sshd`, `avahi-daemon` and `dhcpcd` (multi-user); `playground-modules` (sysinit).
- **Files present**: `/usr/lib/firmware/playground.elf` and `/usr/lib/firmware/playground-starter.elf`, `/usr/bin/rtos-agent` (static aarch64), `/usr/bin/host_ping`, `/mnt/system/ko/cvitek_remoteproc.ko`. No `python3`.
- **MOTD**: names the README without a URL. The repository has no git remote; set `PLAYGROUND_README_URL` when building to put a link there.

### Starter firmware (SDK T-Head toolchain)

```
$ riscv64-unknown-elf-readelf -h -l firmware/build/playground-starter.elf
  Class: ELF64   Machine: RISC-V   Entry point address: 0x9fe00000
  Flags: 0x5, RVC, double-float ABI
  LOAD ... 0x000000009fe00000 0x000000009fe00000
$ nm ... | grep
9fe00000 T _start   9fe00db8 T main_cvirtos   9fe00c9a T pg_print   9fe006ba T playground_app_init
```

The vendor `prvCmdQuRunTask` is absent: `task/comm` is not linked.

### Fixes the real build forced

Each of these was found by the build itself; the logs are kept in `out/build-attempt[1-4].log`.

1. **`rtosbuild.py` compiled `task/main/src/FreeRTOS_asm_vectors.S`.** That file is ARM (Xilinx demo) assembly and fails with `Error: unrecognized opcode 'b .'`. The vendor glob is `file(GLOB _SOURCES "src/*.c" "*.S")`: `*.S` refers to the `task/main` root, so the vendor never builds it. `rtosbuild.py` now uses the same glob.
2. **The per-patch "already applied?" test cannot work for stacked patches.** 0002 and 0007 edit lines that 0001 adds. `build-image.sh` now restores every file the series touches to `sdk_commit`, then applies the series in order. Reruns are safe.
3. **Buildroot `host-tar` refused to configure as root**: `configure: error: you should not run configure as root (set FORCE_UNSAFE_CONFIGURE=1 ...)`, in `sdk/build/br.log`. The SDK container runs as root, which is Milk-V's documented usage. `--docker` now passes `FORCE_UNSAFE_CONFIGURE=1`.
4. **`. /etc/os-release` overwrote the script's `VERSION`.** The first complete image was named after "22.04.4 LTS (Jammy Jellyfish)". `os-release` is now read in a subshell, and the image was rebuilt.

The SDK log also shows a few `make ... Error 1 (ignored)` lines in vendor install steps. The SDK Makefile tolerates these with `-`, as does the stock build.

## Acceptance tests

The image is built (above). Test 8 is a dtb inspection, which PROMPT.md says does not need the carrier, and it passes on the kernel-built dtb. On 2026-11-19 the image ran on a Duo S, booted from SD and reached over Wi-Fi (`wlan0` 192.168.1.110). Tests 3-7 were driven from the build PC with PlatformIO 6.2.0 and paramiko 5.1.0. The output is under "Bench run 2026-11-19" below.

| # | Test | Status | Notes |
| --- | --- | --- | --- |
| 1 | Image identity: ping, SSH root/playground, release file matches `sdk.lock`, `rtos-agent.service` active | **passed on Wi-Fi**; Ethernet not recorded | `ping 192.168.1.110` from the PC: 3/3 replies; SSH as root/playground works; release file matches `sdk.lock` (commit `6f8962c3…`, target `milkv-duos-glibc-arm64-sd`); `rtos-agent`, `sshd`, `avahi-daemon`, `playground-modules` all `active`. The board was on Wi-Fi, and `ip -4 addr` showed no Ethernet address, so the Ethernet first-boot link is not yet shown |
| 2 | `state=running` within 30 s; serial shows `RT:` and `playground starter` | **passed** | `Booting fw image playground.elf` at 10.60 s and `is now up` at 10.70 s after boot (`journalctl -b -k -o short-monotonic`); `state=running`; `rtos-agent log` shows `RTD: playground starter 0.1.0`. UART0 console: the user reported on 2026-11-19 that the serial output is correct; no console capture is attached |
| 3 | PlatformIO `hello` upload returns 0, state stays running, `playground hello` seen, SSH session alive, `uptime` not reset | **passed** | `pio run -t upload` → exit 0; `state=running`; log `RTD: playground hello Nov 19 2026 21:07:22`; the SSH session opened before the upload answered after it; `/proc/uptime` 1515.52 → 1519.92 with the same `boot_id` `e6a267c8-…` |
| 4 | Text file: exit 2, previous program still running | **passed** | `rtos-agent load /tmp/bad.txt` → `bad file: /tmp/bad.txt: not an ELF file`, `exit=2`; `status` afterwards still `state=running` |
| 5 | Lock: upload exits 3; unlock, then upload exits 0 | **passed** | locked: `upload.py` prints `locked` and the unlock hint, exits 3 (`pio run -t upload` shows `*** [upload] Error 3`; `pio` itself returns 1 for any failed target); after `unlock`: `pio run -t upload` → exit 0, `state=running` |
| 6 | Load `hello`, `recover`: starter banner back, starter file still there | **passed** | with `hello` running: `rtos-agent recover` → `recovered /lib/firmware/playground-starter.elf state=running`, exit 0; log `RTD: playground starter 0.1.0`; `playground-starter.elf` still present (260184 bytes, sha256 `f6453cd2…`); same `boot_id` |
| 7 | `mailbox_ping` + `host_ping` 7 → 8 | **passed** | `pio run -t upload` of `mailbox_ping` → exit 0, log `waiting for cmd 0x00000040`; on the board `host_ping 7` → `reply 8 == 7 + 1, ok`, exit 0; from the PC `./host_ping.sh 7` → same, exit 0 (and 41 → 42) |
| 8 | dtb: carrier pads unclaimed; Ethernet and `rtos_cmdqu` present | **passed** | `scripts/check-dtb.py` on `linux_5.10/build/.../sg2000_milkv_duos_glibc_arm64_sd.dtb` (sha256 `8bdb896a4fbaded9…`): `RESULT: PASS` (report in "Verification done here", and in `out/dtb-check.txt`). No enabled node references a `board_carrier.h` pad; spi3, i2c4, pwm0-2, vo and mipi_tx are disabled; ethernet, sd, uart0 and rtos_cmdqu are enabled. On the board: `/dev/cvi-rtos-cmdqu` exists (`crw------- root 10, 57`) and Ethernet carries the SSH session |

To record a result, replace "pending" with the command and its output.

### Bench run 2026-11-19

Duo S, ARM boot switch, booted from the SD image. The eMMC held another system (Armbian-style, eMMC = `mmcblk2`). The user was given Milk-V's "eMMC Erase" steps for `mmcblk2boot0`/`boot1`; whether they completed is not recorded, but the board booted the SD image. Network: Wi-Fi, set up by hand (below). Carrier: not recorded. Commands run as root over SSH:

```
# cat /etc/duo-playground-release
DUO_PLAYGROUND_VERSION=0.1.0
SDK_URL=https://github.com/milkv-duo/duo-buildroot-sdk-v2
SDK_COMMIT=6f8962c394dd0a05729abb089f0feb7d5cc4aa5e
SDK_TARGET=milkv-duos-glibc-arm64-sd
STARTER_FIRMWARE=nogit-b8b8fcf01c77
# systemctl is-active rtos-agent.service sshd avahi-daemon playground-modules.service
active / active / active / active
# rtos-agent status
state=running
firmware=/lib/firmware/playground.elf
lock=unlocked
release=0.1.0
# cat /sys/class/remoteproc/remoteproc0/state
running
# journalctl -b -k | grep -iE "remoteproc|c906"
cvitek-rproc c906l_rproc@0: C906L held in reset until firmware playground.elf is loaded
remoteproc remoteproc0: c906l_rproc@0 is available
remoteproc remoteproc0: powering up c906l_rproc@0
remoteproc remoteproc0: Booting fw image playground.elf, size 260184
remoteproc remoteproc0: no resource table found for this firmware
cvitek-rproc c906l_rproc@0: Started from 0x9fe00000
remoteproc remoteproc0: remote processor c906l_rproc@0 is now up
# rtos-agent log | tail
RTD: [16.242768][cvi_spinlock_init] succeess
RTD: playground starter 0.1.0
# rtos-agent load /tmp/bad.txt     (file contains "hello")
bad file: /tmp/bad.txt: not an ELF file
exit=2      (status afterwards: state=running)
# ls -l /dev/cvi-rtos-cmdqu; ls /lib/firmware/playground*.elf
crw------- 1 root root 10, 57 ... /dev/cvi-rtos-cmdqu
/lib/firmware/playground-starter.elf  /lib/firmware/playground.elf
```

Findings from this run:

- The remoteproc boot path works on hardware: the probe holds the C906L in reset, then auto-boots `playground.elf` at `0x9fe00000` with no resource table, as the driver notes above expect.
- `ip` is BusyBox's, which has no `-br`. Use `ip -4 addr`.
- There is no RTC battery or NTP, so the wall clock starts at the build date (log stamps read `Oct 08`).
- BusyBox `uptime` printed `up 772 days 10:10` while `/proc/uptime` read about 1426 s. The kernel's count is right and the BusyBox display is wrong; the cause is not investigated. Test 3 therefore uses `/proc/uptime` and `boot_id`.
- Boot timing: `Booting fw image playground.elf` at 10.601048 s, `is now up` at 10.697439 s.
- The AIC8800 Wi-Fi modules (`/mnt/system/ko/aic8800_bsp.ko`, `aic8800_fdrv.ko`) load by hand. The image does not load them (PROMPT.md: Wi-Fi out of scope). The vendor `/etc/init.d/S99user` → `duo-init.sh` does not run under this systemd, because the image has no sysv generator.

PC side of tests 3-7 (driver: one paramiko session held open across each step, recording `/proc/uptime`, `/proc/sys/kernel/random/boot_id` and `rtos-agent status` before and after):

```
# test 3
before: /proc/uptime 1515.52   boot_id e6a267c8-bd42-4ecc-a5f5-a084a87af511   state=running
$ (cd examples/platformio/hello; pio run -t upload)  -> exit 0
after (same session): /proc/uptime 1519.92   same boot_id   state=running
RTD: playground hello Nov 19 2026 21:07:22
# test 5
$ rtos-agent lock                              -> locked
$ python3 ../common/upload.py .pio/build/hello/firmware.elf
locked
The board is locked. Open the E-stop, then run:
  ssh root@192.168.1.110 rtos-agent unlock
upload.py exit=3          (through pio: "*** [upload] Error 3", pio exit 1)
$ rtos-agent unlock; pio run -t upload         -> exit 0, state=running
# test 6
$ rtos-agent recover
recovered /lib/firmware/playground-starter.elf state=running
RTD: playground starter 0.1.0
-rw-r--r-- 1 root root 260184 ... /lib/firmware/playground-starter.elf
# test 7
$ (cd examples/platformio/mailbox_ping; pio run -t upload)  -> exit 0
RTD: playground mailbox_ping Nov 19 2026 21:07:50, waiting for cmd 0x00000040
$ host_ping 7                      (board)
host_ping: send ip=6 cmd=0x40 nonce=7
host_ping: reply 8 == 7 + 1, ok
$ ./host_ping.sh 7                 (PC)
host_ping: reply 8 == 7 + 1, ok    exit 0
```

Fixes forced by this run:

- `examples/platformio/mailbox_ping/host_ping.sh` called plain `ssh` from the PC. That path ignored `PIO_DUO_PASS`, and run from a script it failed with `Host key verification failed`. It now goes through `common/duo_ssh.py` (new `main()`: `python3 duo_ssh.py <command>`), the same transport as upload.
- The Debian-packaged `pio` (PlatformIO 4.x) on the build PC crashes on start with the installed click (`'PlatformioCLI' object has no attribute 'resultcallback'`). Use PlatformIO Core 6.x (the official installer, or `pip install platformio` in a venv).

### Bench run 2, 2026-11-20 (`scripts/acceptance.py`)

After a reboot, from the build PC over SSH to `duo-playground.local` (mDNS resolved to `192.168.1.111`, `wlan0`): `PIO_DUO_PASS=playground scripts/acceptance.py`. Full log: `out/acceptance-20261120T022120Z.log`.

| # | result | detail |
| --- | --- | --- |
| 1 | manual | ping 3/3, ssh, release = `sdk.lock`, four services active; no wired interface has carrier, so Ethernet is unproven |
| 2 | PASS | `is now up` at 10.590110 s after boot (UART0 half: see test 2 above) |
| 3 | PASS | upload exit 0, state running, hello banner seen, uptime 557.9 → 561.2 s, boot_id same |
| 4 | PASS | exit 2, state running |
| 5 | PASS | locked `upload.py` exit 3, unlocked `pio` upload exit 0 |
| 6 | PASS | recover exit 0, starter banner seen, starter file present |
| 7 | PASS | upload exit 0, board `host_ping 7` → 8, PC `host_ping.sh 7` → 8 |

`boot_id` `2c26a189-…` was the same from the first command to the last, so no step of the run restarted Linux.

Findings:

- Ethernet is `end0`, not `eth0`: systemd 256 names the device-tree interface by its own scheme. `bm-dwmac 4070000.ethernet` probes and attaches the CV182XA PHY, and the link reads `NO-CARRIER` with no cable. Its MAC `7e:4b:67:25:91:05` is locally administered; whether it changes between boots is not checked.
- The Wi-Fi set up by hand (`playground-wifi.service` + `wpa_supplicant@wlan0`) came back after the reboot without help. DHCP gave a new address (`.110` before, `.111` after), and mDNS covered the change.
- After a reboot the C906 runs the last uploaded program, here `mailbox_ping`, because `load` renames the upload onto `/lib/firmware/playground.elf`, which the driver boots. `recover` starts the starter for this boot only; it leaves `playground.elf` alone, so a later reboot runs the last upload again. This follows PROMPT.md, which gives `recover` no boot-time effect. Whether recover should also reset the boot program is still an open question.

## C906 printf, a stuck mailbox interrupt, and a fast_image oops (2026-11-20)

Found while debugging `examples/platformio/beitlabduino_mailbox`, which froze the C906 on its first command.

- **`pg_print()` formats with the vendor `vsnprintf`.** `freertos/cvitek/common/src/riscv64/snprintf.c` replaces newlib's at link time (the map file shows `playground_rt.c.o (vsnprintf)` resolved there). It handles only `%d %x %s %c %p` and `l`. `%x`/`%p` print every hex digit (`0x%02x` of 0x40 prints `0x00000040`); widths are ignored. Any other letter (`%u`, `%i`, `%f`) leaves the parser in "format" mode, so the next `d x s c p` of the plain text is used as the conversion. `pg_print("blink period %u ms", 300)` takes the `s` of `ms` as `%s`, reads the address 300 and the C906 stops. Measured on the board by watching the A29 GPIO data register from Linux (`devmem 0x03020000`): with that format the blink stopped at the first print; with `"nonce=%u"` (nothing after `%u`) or `%d` it kept running. `__attribute__((format(printf)))` on `pg_print` does not warn, because `%u` is valid printf. Existing formats with the same flaw: `mailbox_ping` (`"nonce=%u reply=%u"` prints `%p` output; the error line `"...%u: no free mailbox slot"` would crash), `heartbeat` (`"bit %u), 1 Hz"` prints garbage), and the starter (`"starter ping %u: mailbox busy"` would crash on that error path).
- **A C906 crash leaves the mailbox interrupt claimed in the C906 PLIC, across remoteproc reloads.** After the crash above, `rtos-agent recover` and new uploads started (A29 blinked) but no mailbox message was ever taken: the mailbox showed the C906's interrupt pending and unmasked (`devmem 0x01900038` = `0x0F`, int_mask 0), and the slots kept `linux_valid=1`. Only a board reboot cleared it. The remoteproc reset does not reset the PLIC, and `irq_init()` (`driver/common/src/system.c`) clears priority, pending and enable but never completes an outstanding claim. Writing the claim/complete register once for the mailbox IRQ before it is used, `mmio_write_32(PLIC_CLAIM, MBOX_INT_C906_2ND)` (0x70200004, 61), released it: a program doing that in its first task answered at once after a crashed one, with no reboot (every one of about ten loads after a crash). Not yet in `playground_rt.c`.
- **`rtos-agent log` against a C906 that does not answer crashes the kernel.** With the C906 stopped, `SYS_CMD_INFO_DUMP_MSG` timed out (`dump_uart_va = 0`) and `cvi_fast_image_dump_snapshot` then took `Unable to handle kernel paging request at virtual address ffffffbf71400010` (Oops in `cv181x_fast_image`, from `rtos-agent`'s ioctl). The board kept running, but the oops left the module in a bad state until reboot. `pio run -t rtmonitor` repeats that ioctl every 2 s, so it should not be left running against a stopped C906.
- **Reading C906 variables from Linux with `devmem` shows stale values.** The C906 data cache is write-back: `xTickCount`, `pxCurrentTCB` and the program's `.bss` read as 0 while the program was visibly running. Peripheral registers (GPIO, mailbox, UART) read correctly.
- **Wi-Fi MAC changes on every boot** (`88:00:33:77:30:72`, then `…:e6:b1`), so DHCP gives a new address each time. mDNS still finds `duo-playground.local`, but the name can take a few minutes to come back after a reboot.


## Wi-Fi provisioning and automatic startup, 2026-11-20 (0.2.0)

The earlier bench notes describe 0.1.0, where Wi-Fi modules were loaded by hand. Version 0.2.0 enables `beitlab-wifi.service` and `beitlab-wpa.service` in the rootfs systemd presets. The driver loader follows the pinned SDK's `device/generic/rootfs_overlay/duos/mnt/system/duo-init.sh`: `aic8800_bsp.ko`, 0.5 seconds, then `aic8800_fdrv.ko`. It waits up to ten seconds for `wlan0`, skips already loaded modules, and reports loading/enumeration failure in the journal. It does not invoke the vendor GPIO/PWM/multimedia setup. A single background dhcpcd instance handles `end0`, `eth0` and `wlan0`, with Ethernet metric 100 and Wi-Fi metric 200.

SDK inspection commands and results:

```sh
sed -n '28,32p' sdk/device/generic/rootfs_overlay/duos/mnt/system/duo-init.sh
# insmod /mnt/system/ko/aic8800_bsp.ko
# sleep 0.5
# insmod /mnt/system/ko/aic8800_fdrv.ko
rg CONFIG_AIC_FW_PATH sdk/linux_5.10/build/sg2000_milkv_duos_glibc_arm64_sd/.config
# CONFIG_AIC_FW_PATH="/mnt/system/firmware/aic8800"
```

The shipped BSP binary contains `/mnt/system/firmware/aic8800` as its firmware path. The rootfs checker verifies that path plus both modules and the four D80 firmware blobs. The Makefile's generic `/vendor/etc/firmware` default is therefore not the path to provision on this target.

`scripts/configure-wifi.py` takes an uncompressed image and creates a private personal copy, or writes to a mounted boot volume. The password is read without echo and never passed as a process argument. The profile uses a hex-encoded SSID, PBKDF2-derived WPA PSK, optional country code and hidden-network scanning. Its image mode uses mtools with an MBR-derived offset; no loop mount/root access or SDK is needed on the flashing PC. It writes an adjacent `.img.sha256`, which `scripts/flash.sh` verifies. The original release checksum is unchanged.

Inspecting the pinned SDK image's MBR and BPB showed partition 1 starts at sector 1 (512 bytes), has partition type 0x0c, and actually contains FAT16 with label `boot`. The helper reads the partition offset and FAT16/FAT32 label fields instead of assuming FAT32 or a fixed offset. Patch `0008-duos-boot-wifi-provisioning.patch` adds `playground/wifi-supported` to the FAT volume from `fs/boot/playground/wifi-supported`. The SDK partitions, `fip.bin` and `boot.sd` stay in place. This capability marker lets the helper reject 0.1.x images that cannot import credentials.

`beitlab-wifi-import.service` mounts `/boot` and runs before `beitlab-wpa.service`. It accepts only the generated profile format, normalizes Windows line endings, installs the profile atomically as root:root mode 0600, syncs, removes the boot provisioning file, and syncs again. Invalid input keeps the previous saved profile and logs an error without printing credentials; WPA can still start with the saved profile. The release image has no Wi-Fi credentials. The personal image and card contain a network secret (the PSK), even though the plaintext password is absent.

The serial wizard remains available when no usable connection appears. Verification caught an echo race: the wizard previously printed its password prompt before disabling terminal echo. Disabling echo first prevents fast input from appearing in the console transcript.

Validation commands/results:

- `python3 -m unittest discover -s scripts/tests -v`: 22 tests, 20 passed; the two real FAT image tests need mtools/dosfstools and are run in the SDK container instead. This includes actual BusyBox execution of the importer, serial wizard and isolated driver loader.
- `docker run --rm -v "$PWD:/home/work" -w /home/work milkvtech/milkv-duo:latest python3 -m unittest discover -s scripts/tests -p test_wifi_provision.py -v`: 8 tests, 5 passed, 3 BusyBox import tests skipped (those pass on the host). Both real FAT image tests passed, including unchanged source image, root-partition sentinel, PSK/SSID contents, personal checksum and refusal of an old image.
- `systemd-analyze verify --root="$PWD/sdk/buildroot/output/milkv-duos-glibc-arm64-sd/target" beitlab-wifi.service beitlab-wifi-import.service beitlab-wpa.service beitlab-network-setup.service`: passed with no diagnostics.
- `bash -n scripts/build-image.sh scripts/flash.sh`, BusyBox shell syntax, and Python compilation: passed. Logs: `out/wifi-tests-0.2.0.log`, `out/wifi-image-tests-0.2.0.log`.

The user confirmed no board is connected. Fresh-card Wi-Fi association, DHCP, mDNS/SSH and automatic reconnect after reboot remain unverified for 0.2.0. The earlier 0.1.0 bench results do not prove this first-boot path. The fresh-card procedure is in `docs/FLASH.md`.

The final boot-volume check exposed a wording error in the contract: `rawimages/boot.sd` is the genimage **input** path. Genimage runs `mcopy .../rawimages/boot.sd ::`, which installs `/boot.sd` on the FAT volume. `u-boot-2021.10/include/configs/cv181x-asic.h:307` confirms `fatload mmc 0 ${uImage_addr} boot.sd`. The checker and contract now name the actual on-card path; the SDK boot layout was preserved. The build completed; the corrected post-build checks were rerun against the completed output without recompiling.

Final build/inspection results:

- `scripts/build-image.sh --docker` built Linux, modules, the rootfs and the SD image in `milkvtech/milkv-duo:latest` (Ubuntu 22.04 amd64). Actual host: Ubuntu 24.04.5 LTS x86_64. Full log: `out/build-0.2.0-wifi.log`; corrected final checks and artifact collection: `out/image-final-checks-0.2.0.log`.
- `out/build-checks.txt`: all kernel, DT, remoteproc, rootfs, Wi-Fi payload/permissions/service ordering and FAT-boot checks passed. `debugfs` confirms the saved-profile inode in the actual ext4 image is mode 0600, uid/gid 0.
- `docker run --rm -v "$PWD:/home/work" -w /home/work milkvtech/milkv-duo:latest python3 out/wifi-final-smoke.py`: passed. The helper personalized the finished 0.2.0 image, read the exact generated SSID/PSK/country back through mcopy, verified its personal checksum, and confirmed the release image, MBR, logo and rootfs were unchanged. Temporary personal-image files were removed automatically. Log: `out/wifi-final-smoke-0.2.0.log`.
- `(cd out && sha256sum -c SHA256SUMS)`: image and agent checksums verified.

Artifacts: `out/beitlab-robotics-milkv-duo-os-0.2.0-duos-arm64-sd.img` (941621760 bytes) and `out/rtos-agent-0.2.0-aarch64`. Image SHA256: `2e6739839dd7778f74771ee08f4e72b981b9a3ddda5647042841c7f530599540`. These are built artifacts with offline verification; first-boot radio connectivity still awaits the hardware test noted above.

## Combined first-boot customization, 2026-11-20 (0.3.0)

`scripts/configure-image.py` provides a combined entry point for the existing PC provisioning code. It accepts Wi-Fi SSID/password/country, `--hostname` and repeatable `--ssh-key` public-key files. `--no-wifi` allows Ethernet-only hostname/key setup without prompting for a network password. The existing `configure-wifi.py` interface and Wi-Fi-only 0.2.0 images remain supported. Both mounted-card and personal-image modes accept the new settings. Original images and non-boot partitions remain unchanged; personal images get their own checksum. The helper refuses existing output files and validates hostnames and OpenSSH public-key fields, rejecting private keys and truncated keys.

The new FAT marker `playground/setup-supported` distinguishes the full customization capability from 0.2.0's Wi-Fi-only marker. Patch 0008 includes both markers without changing the SDK partition layout. The helper rejects an older image when hostname/key customization is requested. The boot files are `playground/wifi.conf`, `playground/hostname` and `playground/authorized_keys`.

`beitlab-hostname.service` imports the validated hostname before dhcpcd, Avahi, SSH and the WPA service start. It updates `/etc/hostname`, the running kernel hostname, the `127.0.1.1` entry in `/etc/hosts`, and the serial banner; then it syncs and removes the boot hostname file. Invalid input keeps the previous name. The existing `playground-keys.service` imports public keys before SSH starts, preserving password login and deduplicating repeated imports. Its input reader now accepts a final key without a newline, including Windows CRLF. The C906/agent startup retains no dependency on these provisioning services or network availability.

Validation:

- `python3 -m unittest discover -s scripts/tests -v`: 31 tests, 27 passed and four real-FAT tests skipped on the host (mtools is in the SDK container). Log: `out/setup-tests-0.3.0.log`.
- `docker run --rm -v "$PWD:/home/work" -w /home/work milkvtech/milkv-duo:latest python3 -m unittest discover -s scripts/tests -p test_wifi_provision.py -v`: all four real-FAT tests pass, including combined settings, unchanged release/rootfs, output checksums and refusal of Wi-Fi-only images for hostname setup. The six BusyBox import tests run on the host instead. Log: `out/setup-fat-tests-0.3.0.log`.
- A temporary real `ssh-keygen -t ed25519` key was accepted by the PC helper; the combined CLI wrote the expected public key and lowercase hostname to a mock mounted boot volume with `--no-wifi`, without creating a Wi-Fi profile.

Fresh-card hostname discovery, Wi-Fi connectivity and key-authenticated SSH still need a physical board test. The earlier bench results and 0.2.0 image do not validate this new first-boot customization path. The user procedure is in `docs/FLASH.md`.

Build and finished-image validation:

- `scripts/build-image.sh --docker`: completed successfully; full log `out/build-0.3.0.log`. Kernel/DT/remoteproc, private profile, service enablement and both boot capability marker checks pass in `out/build-checks.txt`. The release image contains no personal settings.
- `systemd-analyze --root="$PWD/sdk/buildroot/output/milkv-duos-glibc-arm64-sd/target" verify beitlab-hostname.service playground-keys.service beitlab-wifi-import.service beitlab-wpa.service beitlab-network-setup.service rtos-agent.service`: passed with no diagnostics; `out/setup-systemd-0.3.0.log`.
- `debugfs` extracted the hostname importer from the actual generated ext4 filesystem; its bytes match the source, including runtime hostname, hosts and banner updates.
- `docker run --rm -v "$PWD:/home/work" -w /home/work milkvtech/milkv-duo:latest python3 out/setup-final-smoke.py`: personalized the finished release with Wi-Fi, hostname and a temporary real Ed25519 public key, read all exact settings back from FAT, verified the personal checksum, and confirmed the source image, MBR, logo and root partition were unchanged. Temporary personal images/keys were removed. Log: `out/setup-final-smoke-0.3.0.log`.
- Real `ssh-keygen` Ed25519, RSA and ECDSA public keys all passed the PC parser. `(cd out && sha256sum -c SHA256SUMS)` verified both release artifacts.

Built image: `out/beitlab-robotics-milkv-duo-os-0.3.0-duos-arm64-sd.img`, 941621760 bytes, SHA256 `ac469f8bb0e4ee6dd542124553bbbb839ec30b1e1713427a526eeca79afae5cb`. Agent: `out/rtos-agent-0.3.0-aarch64`. Hardware validation remains pending as noted above.

## Image identity and upstream license review (2026-11-20)

Read the existing 0.2.0 image's MBR and third partition into a temporary
file, then used read-only `debugfs -R 'cat /usr/lib/os-release'` and the
same command for `/etc/hostname`, `/etc/issue` and
`/etc/duo-playground-release`. Result: OS name
`beitlab Robotics MilkV duo OS 0.2.0`, hostname `beitlab-milkv-duo`, with
SDK commit `6f8962c394dd0a05729abb089f0feb7d5cc4aa5e` preserved.
The image SHA256 remains
`2e6739839dd7778f74771ee08f4e72b981b9a3ddda5647042841c7f530599540`.

`git -C sdk ls-tree --name-only HEAD` shows no SDK-wide license file.
Component licenses and unresolved vendor licensing questions are recorded
in [LICENSING.md](LICENSING.md). Added `THIRD_PARTY_NOTICES` and staged
16 upstream notice/license files in the rootfs overlay. Each is verified
against `git show <pinned-commit>:<source-path>` (the FreeRTOS header entry
is the verbatim initial comment); the manifest records the paths and hashes.
The existing 0.2.0 image was not modified. A separate 0.3.0 build was already
running and had copied its overlay, so no notice-inclusion claim is made
for that image either. Companion selected notices:
`out/beitlab-os-selected-upstream-notices-2026-11-20.zip` with its own
`.sha256`; archive integrity and all 16 manifest hashes passed.
Full Buildroot legal-info, corresponding-source delivery and vendor
firmware/runtime permission resolution remain release work; the selected
notice archive is not a complete compliance bundle.


## Serial Wi-Fi password input regression, 2026-11-20

Hardware report: the serial wizard printed `# reading passphrase from stdin`,
`tcgetattr: Inappropriate ioctl for device`, then `Could not generate the Wi-Fi
profile`. The built wpa_supplicant 2.11 helper calls `tcgetattr(STDIN_FILENO)`
unconditionally when the password is read from stdin. The wizard correctly
passes the password through a pipe, keeping it out of process arguments;
that pipe is not a terminal. The old test mock omitted this terminal check.

`image/patches/0009-wpa-passphrase-piped-password.patch` adds a Buildroot
package patch that limits terminal echo handling to terminal stdin. Interactive
input keeps its hidden-password behavior, and pipe input works again. No
plaintext-password argument or file is needed. On an existing SDK build,
run `make -C sdk/buildroot O="$PWD/sdk/buildroot/output/milkv-duos-glibc-arm64-sd" wpa_supplicant-dirclean`
before `scripts/build-image.sh --docker` so Buildroot re-extracts and patches
this already-built package. Run the make command in the supported build
container when required by the host setup.

Verification (native build of the actual SDK wpa_supplicant 2.11 sources,
minimal `.config` with `CONFIG_TLS=openssl` and `CONFIG_CTRL_IFACE=y`, then
`make -C <copy>/wpa_supplicant -j4 wpa_passphrase`):

- Original helper with piped stdin reproduced the exact `tcgetattr` failure.
- SDK `git apply --check` and inner package `patch -p1 --dry-run` passed.
- Patched helper accepted piped passwords and generated PSKs matching Python
  PBKDF2-HMAC-SHA1, including 8-byte and 63-byte passwords.
- `WIFI_TEST_WPA_PASSPHRASE=<native-patched-binary> python3 -m unittest discover -s scripts/tests -p test_wifi_setup.py -v`:
  all 10 tests passed, including hidden input, special SSIDs, retry, cancellation
  and restoration of the previous profile.
- A BusyBox PTY check with the original helper confirmed the console workaround:
  `WIFI_PSK=$(wpa_passphrase "$WIFI_SSID" | sed -n 's/^[[:space:]]*psk=//p')`
  reads directly from the terminal, keeps password echo off, and extracts only
  the derived key. Writing a hex SSID and that PSK with umask 077 produced the
  expected mode-0600 profile without a plaintext password.

The source fix has not been rebuilt into a release image in this session.
Radio association and DHCP still require the user's board check.


## Guided PC imager, 2026-11-20

`scripts/beitlab-imager.py` adds a cyan/white beitlab terminal wizard with optional emojis, progress indicators and four stages: image verification, first-boot customization, removable-drive selection, and write/read-back verification. It reuses `configure-wifi.py`; the image's provisioning markers determine which settings/account model are offered. Wi-Fi password entry and confirmation use getpass with its echoing fallback refused. The settings include hostname where supported and regular-user public-key/password SSH on 0.4.0 images. Personal copies are temporary by default; `--output` keeps a private copy/checksum. The CLI does not rebuild or alter the Linux/C906 sources.

Local `--image` takes priority. Future public GitHub downloads use a repository from `--github-repo`, `BEITLAB_IMAGE_REPO`, `imager.json` or a GitHub origin remote, in that order. No public repository was invented/configured; the current JSON value is null. With no repository or explicit image, a local-path prompt is shown. The latest published release (or `--release` tag) is queried using the GitHub REST API. Only the beitlab Duo S ARM64 image asset pattern is accepted. Downloads require HTTPS and a GitHub SHA256 digest or matching checksum asset; failed partial downloads are removed. The source/release image stays unchanged.

Drive discovery is read-only. The Linux backend uses explicit lsblk JSON columns, findmnt device IDs for system/home/source paths, active-mapping checks, and the kernel disk sequence when available. The macOS backend resolves APFS containers to physical stores to protect the system disk. Before writing, the privileged worker independently rechecks identity/capacity and the prepared-image checksum, requires an interactive exact `ERASE <device>` phrase, unmounts volumes and rechecks the device. Linux also exclusively opens the block device and verifies its major/minor, size and disk sequence through the open descriptor before the first write. The writer handles short writes, fsyncs and verifies a full image-length read-back hash. A changed/replaced target, checksum mismatch, failed unmount, noninteractive session or wrong confirmation aborts. Discovery/dry-run/preparation paths never open a real disk for writing. Native Windows raw-disk flashing is explicitly unsupported in this version.

Validation:

- `python3 -m unittest discover -s scripts/tests -v`: 72 tests, 66 passed and six skipped for host-only missing tools/fixtures. Log: `out/imager-regression-tests.log`.
- `docker run --rm -v "$PWD:/home/work" -w /home/work milkvtech/milkv-duo:latest python3 -m unittest discover -s scripts/tests -p test_imager.py -v`: all 26 imager tests passed. This includes an actual PTY wizard run with a real FAT image, hidden-password transcript checks, exact boot settings, output permissions/checksum and unchanged release image. Log: `out/imager-fat-tests.log`.
- Safety tests cover system/internal/read-only/empty/mapped/source disks, drive replacement before writing, cancellation without unmount/open, changed images, short writes, read-back mismatch, APFS backing-store protection, and preservation of existing files during decompression failure. GitHub API/download behavior is mocked; no release was published or downloaded from a live configured repository.
- `python3 scripts/beitlab-imager.py --list-drives`: real read-only discovery blocked both internal NVMe disks and the empty SD/MMC readers. No eligible drive was present. Log: `out/imager-drive-discovery.log`.
- Python compilation and CLI help passed. No real storage device was written. Physical Linux/macOS flashing and first-boot connectivity remain pending; macOS discovery/write calls have fixture coverage only. Usage and deployment settings are in `docs/FLASH.md`.

Primary references consulted for host integration: [lsblk](https://man7.org/linux/man-pages/man8/lsblk.8.html), the installed `/usr/include/linux/fs.h` definitions for `BLKGETSIZE64`/`BLKGETDISKSEQ`, and [GitHub Releases API](https://docs.github.com/en/rest/releases/releases). No SDK pin or remoteproc upload behavior changed for this PC-side tool.

## Regular user and SSH defaults, OS 0.4.0 (2026-11-20)

The user selected SSH keys by default, with password SSH only when
explicitly enabled. This supersedes the old root/playground defaults in
PROMPT.md. The new account is `beitlab`, UID/GID 1000, with a mode-0700
home and membership in `wheel` and `systemd-journal`. Both release password
fields are locked (`*`); root SSH is prohibited. General sudo requires a
chosen user password. Passwordless sudo is limited to a root-owned helper
whose allowlist and stdin-only firmware load do not accept arbitrary file
paths or commands. The agent remains root-only and not setuid, and its
remoteproc reload/recovery paths are unchanged.

The PC helper prompts for an optional confirmed user password (at least
12 characters), hashes it with OpenSSL SHA-512 crypt and puts only the
hash plus an explicit SSH-password-login choice in `playground/user.conf`.
Regular-user key/password provisioning requires `user-supported`, so it
cannot silently customize older root-login images. Keys are installed
for beitlab and consumed from the boot partition. Key-only provisioning
unlocks the account with an undisclosed random hash: OpenSSH without PAM
rejects locked accounts even with a valid public key. The J22 initial-user
wizard can set a local/admin password with hidden, confirmed input,
minimum length and a bounded timeout. The firmware agent starts first.

Buildroot evidence: `system/Config.in` defines disabling
`BR2_TARGET_ENABLE_ROOT_LOGIN` as locking password login; explicit
`PermitRootLogin no` is also required to prohibit key-based root SSH.
`package/sudo/sudo.mk` provides the package's mode-4755 root-owned sudo
binary through its permissions table. Patch 0009 enables sudo and the
user table without changing the pinned SDK revision. Device permissions
enforce the private home and mode-0440 root-owned sudoers policy.

The SDK ships a SysV inittab entry calling
`/usr/local/bin/autologin`, whose original content was `login -f root`.
Systemd does not run that entry in this image; nonetheless the overlay
now replaces that legacy helper with ordinary `login`, eliminating the
credential bypass even if it is invoked later. Active service files
were inspected for autologin options. Rootfs checks also reject shared SSH
host private keys; the stock OpenSSH unit generates host keys at boot.

Verification completed before final image publication:

- `python3 -m unittest discover -s scripts/tests -p test_user_security.py -v`:
  10 passed. Includes malformed boot profiles, root remaining locked,
  key-only account handling, helper argument/path refusal, oversized
  uploads, private staging/cleanup, and real BusyBox serial terminal
  password privacy/echo restoration and weak-password refusal.
- `python3 -m unittest discover -s scripts/tests -p test_user_upload.py -v`:
  2 passed. Normal-user upload uses the home directory plus stdin to the
  restricted helper, preserves agent exit codes 0/2/3/4 and cleanup; the
  explicit legacy-root option retains its old agent command.
- Wi-Fi/provisioning suite: 32 tests on the host, 27 passed and five FAT
  tests skipped there; the SDK container passed those five FAT tests as
  part of its 18-test provisioning run. Account profiles round-trip as
  hashes and old images without `user-supported` are rejected.
- `out/security-login-smoke.py` starts an isolated Docker OpenSSH server
  bound only to host loopback, runs the actual user/key import scripts
  against a real test account, and uses verified host keys. Results:
  key login as UID 1000 passed; root key login and default password SSH
  were refused; explicitly enabled password SSH accepted the chosen
  password. All test keys/passwords are temporary test-only fixtures.
  Log: `out/security-login-smoke-0.4.0.log`.
- `visudo -cf image/overlay/etc/sudoers`: passed. Systemd verification of
  the user/key/SSH/serial setup units against the generated target root
  passed without diagnostics. Python and shell syntax checks passed.

The initial full build passed its account/permission checks. Comparing
its console script with the final source then caught stale SDK staging.
A rootfs-only repack was used to synchronize it. Important SDK detail:
the SDK cleans its generated Buildroot overlay after the full build;
regenerating only two files there loses other overlay settings on a
standalone Buildroot run. The post-build check caught the missing custom
sudoers policy. Restoring the complete merged `install/.../tmp-rootfs`
overlay, then applying `image/overlay`, restores the exact full-build
configuration before rootfs regeneration. No kernel/firmware rebuild is
needed for this packaging correction. Final checks must pass against the
repacked rootfs before its image is copied or its checksums are updated.

No Duo S is connected. Real first-boot association, local/sudo login and
firmware upload on hardware remain unverified for 0.4.0; the isolated SSH
and host helper tests do not substitute for that test. See SECURITY.md.

Final 0.4.0 target-tool inspection found that the pinned BusyBox omitted
`CONFIG_CHPASSWD` and used `FEATURE_DEFAULT_PASSWD_ALGO="md5"`.
`image/busybox-user.config`, installed through patch 0009's Buildroot
fragment setting, now enables chpasswd and makes password changes default
to SHA-512. BusyBox was rebuilt and the filesystem/image regenerated with
the complete merged overlay. The final checker requires both the applet
and these built configuration settings. The SDK legacy autologin shim and
console setup script were compared byte-for-byte with the final tarball.

Final results: `out/security-final-checks-0.4.0.log` passed all kernel,
DT/carrier, account/permissions, BusyBox, Wi-Fi, service-ordering and boot
marker checks. `out/user-final-smoke-0.4.0.log` passed personalization of
the finished image with Wi-Fi, hostname, regular-user hash and public key;
the original image, partition table and non-boot contents stayed unchanged.
Image and agent checksums verified. Selected upstream notices from the
previous license review are included in this image; this does not resolve
the outstanding complete source/vendor permission review.

Final image SHA256:
`945c43d5bcb66379fe153f0bfa5220daea6777688da1625a400d2df9e84e5164`.
The 941621760-byte image and `beitlab-first-boot-tools-0.4.0.zip` were
copied to the established Samba directory `/mnt/projects/duo-s-playground`,
each with an adjacent `.sha256`. Both destination copies were read back
and their checksums matched. The local 0.4.0 build artifact remains in out/.


### Imager confirmation and cancellation correction, 2026-11-20

A user entered `ERASE` when the required phrase was `ERASE /dev/sda`.
The old writer treated any mismatch as cancellation before unmounting or
writing. Both the privileged worker and its parent printed the same
cancellation message. No data was written in the reported attempt.

The writer now retries partial/mistyped confirmation while keeping the
prepared settings, and still requires the full device-specific phrase.
Empty input, `q`, `quit`, `cancel`, Ctrl-C or EOF can cancel. The worker
tracks whether the byte-writing phase was reached, so cancellation before
writing says the drive was unchanged; interruption during writing asks
for a reflash. The parent waits for the foreground worker's Ctrl-C handling
and propagates its exit status without repeating the child's message.

`python3 -m unittest discover -s scripts/tests -p test_imager.py -v`:
29 tests passed, including confirmation retry without unmount/open,
worker cancellation reported once, pre-write cancellation without a
reflash warning, and the existing FAT/PTY workflow tests. Log:
`out/imager-confirmation-tests.log`. Python compilation also passed.
No physical drive was written; the Linux image does not need rebuilding
for this PC-side CLI correction.
