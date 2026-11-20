/* SPDX-License-Identifier: MIT */
/*
 * C906L firmware validation. Mirrors firmware/rtosbuild.py check_elf().
 *
 * The rules come from the pinned driver and loader (docs/sdk-notes.md):
 *  - linux_5.10/drivers/remoteproc/remoteproc_elf_loader.c accepts ELF32 or
 *    ELF64, little endian, needs at least one PT_LOAD, filesz <= memsz and
 *    offset + filesz inside the file, and copies each PT_LOAD to the
 *    carveout through p_paddr.
 *  - cvitek_remoteproc.c (image/patches/0001) registers only the rtos_dram
 *    carveout, so every PT_LOAD must fall inside it.
 *  - cvitek_rproc_start() only pulses the C906L reset; the reset vector is
 *    the address FSBL wrote (CVIMMAP_FSBL_C906L_START_ADDR), so e_entry
 *    must equal it.
 *  - The C906L toolchain flags are -march=rv64imafdc -mabi=lp64d, so the
 *    file must be ELF64 EM_RISCV with the double-float ABI flag.
 *  - The vendor linker script places .vectors (_start) first in .text, so
 *    a .text section must exist and contain e_entry.
 *  - No .resource_table is required: 06c23aef made it optional.
 */
#include <stdio.h>
#include <string.h>
#include "elfcheck.h"

#define EM_RISCV 243
#define ET_EXEC 2
#define PT_LOAD 1
#define SHT_PROGBITS 1
#define SHF_EXECINSTR 0x4
#define EF_RISCV_FLOAT_ABI 0x6
#define EF_RISCV_FLOAT_ABI_DOUBLE 0x4

static uint16_t rd16(const uint8_t *p) { return p[0] | p[1] << 8; }
static uint32_t rd32(const uint8_t *p) { return rd16(p) | (uint32_t)rd16(p + 2) << 16; }
static uint64_t rd64(const uint8_t *p) { return rd32(p) | (uint64_t)rd32(p + 4) << 32; }

#define FAIL(...) do { snprintf(why, why_len, __VA_ARGS__); return -1; } while (0)

int elf_check(const uint8_t *d, size_t size, const struct elf_rules *r,
	      char *why, size_t why_len)
{
	uint64_t entry, phoff, shoff, end = r->region_base + r->region_size;
	uint32_t flags;
	uint16_t phentsize, phnum, shentsize, shnum, shstrndx;
	unsigned loads = 0, i;
	int have_text = 0;

	if (size < 64 || memcmp(d, "\177ELF", 4) != 0)
		FAIL("not an ELF file");
	if (d[4] != 2)
		FAIL("not a 64-bit ELF (class %u)", d[4]);
	if (d[5] != 1)
		FAIL("not little endian");
	if (rd16(d + 18) != EM_RISCV)
		FAIL("not RISC-V (e_machine %u)", rd16(d + 18));
	if (rd16(d + 16) != ET_EXEC)
		FAIL("not an executable (e_type %u)", rd16(d + 16));
	flags = rd32(d + 48);
	if ((flags & EF_RISCV_FLOAT_ABI) != EF_RISCV_FLOAT_ABI_DOUBLE)
		FAIL("float ABI is not lp64d (e_flags 0x%x); build with the SDK flags", flags);
	entry = rd64(d + 24);
	if (entry != r->boot_addr)
		FAIL("entry 0x%llx is not the C906L boot address 0x%llx",
		     (unsigned long long)entry, (unsigned long long)r->boot_addr);

	phoff = rd64(d + 32);
	phentsize = rd16(d + 54);
	phnum = rd16(d + 56);
	if (phnum == 0 || phentsize != 56 || phoff > size || phnum * 56ull > size - phoff)
		FAIL("bad program header table");
	for (i = 0; i < phnum; i++) {
		const uint8_t *ph = d + phoff + i * 56ull;
		uint64_t off = rd64(ph + 8), paddr = rd64(ph + 24);
		uint64_t filesz = rd64(ph + 32), memsz = rd64(ph + 40);

		if (rd32(ph) != PT_LOAD || memsz == 0)
			continue;
		loads++;
		if (filesz > memsz)
			FAIL("segment %u: filesz > memsz", i);
		if (off > size || filesz > size - off)
			FAIL("segment %u: truncated file", i);
		if (paddr < r->region_base || memsz > end - paddr || paddr > end)
			FAIL("segment %u [0x%llx, 0x%llx) is outside the C906L region [0x%llx, 0x%llx)",
			     i, (unsigned long long)paddr, (unsigned long long)(paddr + memsz),
			     (unsigned long long)r->region_base, (unsigned long long)end);
	}
	if (!loads)
		FAIL("no loadable segment");

	shoff = rd64(d + 40);
	shentsize = rd16(d + 58);
	shnum = rd16(d + 60);
	shstrndx = rd16(d + 62);
	if (shnum == 0 || shentsize != 64 || shoff > size || shnum * 64ull > size - shoff ||
	    shstrndx >= shnum)
		FAIL("no section header table");
	{
		const uint8_t *str = d + shoff + shstrndx * 64ull;
		uint64_t stroff = rd64(str + 24), strsz = rd64(str + 32);

		if (stroff > size || strsz > size - stroff)
			FAIL("bad section name table");
		for (i = 0; i < shnum; i++) {
			const uint8_t *sh = d + shoff + i * 64ull;
			uint32_t name = rd32(sh);
			uint64_t addr = rd64(sh + 16), sz = rd64(sh + 32);

			if (name >= strsz || strsz - name < 6)
				continue;
			if (memcmp(d + stroff + name, ".text", 6) != 0)
				continue;
			if (rd32(sh + 4) == SHT_PROGBITS && (rd64(sh + 8) & SHF_EXECINSTR) &&
			    entry >= addr && entry < addr + sz)
				have_text = 1;
		}
	}
	if (!have_text)
		FAIL("no executable .text section containing the entry point");
	return 0;
}
