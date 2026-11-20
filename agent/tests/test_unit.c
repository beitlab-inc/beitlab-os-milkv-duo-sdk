/* SPDX-License-Identifier: MIT */
/*
 * Unit tests for elfcheck.c and sha256.c, plus an ELF writer the CLI test
 * uses:  test_unit emit <kind> <file> <boot> <base> <size>
 * kinds: good, failstart (valid, carries the FAILSTART marker the test
 * build of rtos-agent treats as "kernel refused to start").
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "elfcheck.h"
#include "sha256.h"

static int failures;
#define CHECK(cond, msg) do { if (!(cond)) { printf("FAIL: %s\n", msg); failures++; } \
	else printf("ok: %s\n", msg); } while (0)

static void w16(uint8_t *p, uint16_t v) { p[0] = v; p[1] = v >> 8; }
static void w32(uint8_t *p, uint32_t v) { w16(p, v); w16(p + 2, v >> 16); }
static void w64(uint8_t *p, uint64_t v) { w32(p, v); w32(p + 4, v >> 32); }

struct opts {
	int cls, machine, type;
	uint32_t flags;
	uint64_t entry, paddr, memsz;
	int ptype, with_text, marker;
};

/* Layout: ehdr(64) phdr(56) payload(64) shstrtab(17) pad shdrs(3*64) */
static size_t make_elf(uint8_t *b, const struct opts *o)
{
	const char strtab[] = "\0.text\0.shstrtab";
	size_t payload = 120, str = 184, sh = 208;

	memset(b, 0, 512);
	memcpy(b, "\177ELF", 4);
	b[4] = o->cls; b[5] = 1; b[6] = 1;
	w16(b + 16, o->type); w16(b + 18, o->machine); w32(b + 20, 1);
	w64(b + 24, o->entry); w64(b + 32, 64); w64(b + 40, sh);
	w32(b + 48, o->flags); w16(b + 52, 64); w16(b + 54, 56); w16(b + 56, 1);
	w16(b + 58, 64); w16(b + 60, 3); w16(b + 62, 2);
	/* phdr */
	w32(b + 64, o->ptype); w32(b + 68, 5); w64(b + 72, payload);
	w64(b + 80, o->paddr); w64(b + 88, o->paddr); w64(b + 96, 64);
	w64(b + 104, o->memsz); w64(b + 112, 8);
	/* payload */
	memset(b + payload, 0x13, 64); /* riscv nop-ish filler */
	if (o->marker)
		memcpy(b + payload + 8, "FAILSTART", 9);
	memcpy(b + str, strtab, sizeof(strtab));
	/* shdr 1: .text (or .data) */
	w32(b + sh + 64, o->with_text ? 1 : 7);
	w32(b + sh + 64 + 4, 1);
	w64(b + sh + 64 + 8, 0x6); /* ALLOC|EXECINSTR */
	w64(b + sh + 64 + 16, o->paddr); w64(b + sh + 64 + 24, payload);
	w64(b + sh + 64 + 32, 64);
	/* shdr 2: .shstrtab */
	w32(b + sh + 128, 7); w32(b + sh + 128 + 4, 3);
	w64(b + sh + 128 + 24, str); w64(b + sh + 128 + 32, sizeof(strtab));
	return sh + 3 * 64;
}

static struct opts good(uint64_t boot)
{
	struct opts o = { 2, 243, 2, 0x5, boot, boot, 0x1000, 1, 1, 0 };
	return o;
}

int main(int argc, char **argv)
{
	uint8_t b[512];
	char why[256], hex[65];
	struct elf_rules r = { 0x9fe00000ull, 0x9fe00000ull, 0x200000ull };
	struct opts o;
	size_t n;

	if (argc == 7 && !strcmp(argv[1], "emit")) {
		FILE *f;
		r.boot_addr = strtoull(argv[4], NULL, 0);
		o = good(r.boot_addr);
		o.marker = !strcmp(argv[2], "failstart");
		n = make_elf(b, &o);
		f = fopen(argv[3], "wb");
		if (!f || fwrite(b, 1, n, f) != n)
			return 1;
		fclose(f);
		return 0;
	}

	sha256_hex("abc", 3, hex);
	CHECK(!strcmp(hex, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
	      "sha256(abc)");
	sha256_hex("", 0, hex);
	CHECK(!strcmp(hex, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
	      "sha256(empty)");
	{
		static char m[1000001];
		memset(m, 'a', 1000000);
		sha256_hex(m, 1000000, hex);
		CHECK(!strcmp(hex, "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"),
		      "sha256(million a)");
	}

	o = good(r.boot_addr);
	n = make_elf(b, &o);
	CHECK(elf_check(b, n, &r, why, sizeof(why)) == 0, "valid C906L ELF accepted");

	CHECK(elf_check((const uint8_t *)"hello world\n", 12, &r, why, sizeof(why)) && strstr(why, "not an ELF"),
	      "text file rejected");

	o = good(r.boot_addr); o.cls = 1; n = make_elf(b, &o);
	CHECK(elf_check(b, n, &r, why, sizeof(why)) && strstr(why, "64-bit"), "ELF32 rejected");

	o = good(r.boot_addr); o.machine = 183; n = make_elf(b, &o);
	CHECK(elf_check(b, n, &r, why, sizeof(why)) && strstr(why, "RISC-V"), "AArch64 rejected");

	o = good(r.boot_addr); o.flags = 0x1; n = make_elf(b, &o);
	CHECK(elf_check(b, n, &r, why, sizeof(why)) && strstr(why, "lp64d"), "soft-float rejected");

	o = good(r.boot_addr); o.entry += 4; n = make_elf(b, &o);
	CHECK(elf_check(b, n, &r, why, sizeof(why)) && strstr(why, "entry"), "wrong entry rejected");

	o = good(r.boot_addr); o.paddr = 0x80000000; o.entry = 0x80000000;
	r.boot_addr = 0x80000000; n = make_elf(b, &o);
	CHECK(elf_check(b, n, &r, why, sizeof(why)) && strstr(why, "outside"),
	      "segment outside carveout rejected");
	r.boot_addr = 0x9fe00000ull;

	o = good(r.boot_addr); o.memsz = 0x200001; n = make_elf(b, &o);
	CHECK(elf_check(b, n, &r, why, sizeof(why)) && strstr(why, "outside"),
	      "segment past carveout end rejected");

	o = good(r.boot_addr); o.ptype = 4; n = make_elf(b, &o);
	CHECK(elf_check(b, n, &r, why, sizeof(why)) && strstr(why, "loadable"), "no PT_LOAD rejected");

	o = good(r.boot_addr); o.with_text = 0; n = make_elf(b, &o);
	CHECK(elf_check(b, n, &r, why, sizeof(why)) && strstr(why, ".text"), "no .text rejected");

	o = good(r.boot_addr); n = make_elf(b, &o);
	CHECK(elf_check(b, 100, &r, why, sizeof(why)) != 0, "truncated file rejected");

	printf("%s: %d failure(s)\n", failures ? "FAILED" : "PASSED", failures);
	return failures ? 1 : 0;
}
