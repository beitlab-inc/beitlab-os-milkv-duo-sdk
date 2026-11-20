/* SPDX-License-Identifier: MIT */
/*
 * rtos-agent: load, restart and inspect the C906L program on a Duo S
 * playground image through the kernel remoteproc class.
 *
 *   rtos-agent status
 *   rtos-agent load <path-to-elf>
 *   rtos-agent recover
 *   rtos-agent log [-f]
 *   rtos-agent lock
 *   rtos-agent unlock
 *   rtos-agent boot          (run by rtos-agent.service; not for users)
 *
 * Exit codes: 0 ok, 1 usage/system error, 2 bad file, 3 locked,
 * 4 the C906 did not reach "running" (the starter was restarted).
 *
 * The program talks only to local files: sysfs, /lib/firmware, /etc and
 * /dev/cvi-fast-image. It opens no socket; SSH is its only remote access.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <syslog.h>
#include <time.h>
#include <unistd.h>

#include "elfcheck.h"
#include "sha256.h"
#include "vendor_abi.h"

/* Filled in by agent/Makefile from the board memmap (cvi_board_memmap.h).
 * Never edit these here. */
#ifndef PG_C906L_BOOT_ADDR
#error "PG_C906L_BOOT_ADDR (CVIMMAP_FSBL_C906L_START_ADDR) must be passed by the build"
#endif
#ifndef PG_RTOS_BASE
#error "PG_RTOS_BASE (CVIMMAP_FREERTOS_ADDR) must be passed by the build"
#endif
#ifndef PG_RTOS_SIZE
#error "PG_RTOS_SIZE (CVIMMAP_FREERTOS_SIZE) must be passed by the build"
#endif

#define EXIT_OK 0
#define EXIT_ERR 1
#define EXIT_BADFILE 2
#define EXIT_LOCKED 3
#define EXIT_NOSTART 4

#define RPROC "/sys/class/remoteproc/remoteproc0"
#define FW_DIR "/lib/firmware"
#define FW_NAME "playground.elf"
#define STARTER_NAME "playground-starter.elf"
#define LOCK_FILE "/etc/rtos-agent/lock"
#define RELEASE_FILE "/etc/duo-playground-release"
#define RUN_LOCK "/run/rtos-agent.lock"
#define FAST_IMAGE_DEV "/dev/" FAST_IMAGE_DEV_NAME
#define CONSOLE_UART "/dev/ttyS0"
#define DT_CARVEOUT_GLOB "/proc/device-tree/reserved-memory/rtos_dram@*/reg"
#define START_TIMEOUT_MS 5000
#define BOOT_TIMEOUT_MS 20000
#define MAX_ELF (32u << 20)

static const char *prog = "rtos-agent";

/* ------------------------------------------------------------------ */
/* Paths. A test build (-DRTOS_AGENT_TESTING) prefixes every path with  */
/* $RTOS_AGENT_ROOT and emulates the remoteproc state attribute.        */

static const char *path(const char *p)
{
#ifdef RTOS_AGENT_TESTING
	static char bufs[4][512];
	static int n;
	const char *root = getenv("RTOS_AGENT_ROOT");
	char *b = bufs[n++ & 3];

	snprintf(b, sizeof(bufs[0]), "%s%s", root ? root : "", p);
	return b;
#else
	return p;
#endif
}

static void logmsg(int prio, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void logmsg(int prio, const char *fmt, ...)
{
	va_list ap;

	va_start(ap, fmt);
	vsyslog(prio, fmt, ap);
	va_end(ap);
}

static int read_text(const char *file, char *buf, size_t len)
{
	int fd = open(file, O_RDONLY | O_CLOEXEC);
	ssize_t n;

	if (fd < 0)
		return -1;
	n = read(fd, buf, len - 1);
	close(fd);
	if (n < 0)
		return -1;
	buf[n] = '\0';
	while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == ' '))
		buf[--n] = '\0';
	return 0;
}

#ifdef RTOS_AGENT_TESTING
/* Emulate the kernel: "start" boots unless the firmware file contains
 * the marker FAILSTART; "stop" stops; anything else is EINVAL. */
static int fake_state_write(const char *file, const char *val)
{
	char fw[256], fwpath[512], st[32], buf[4096];
	const char *res;
	int fd;
	ssize_t n;

	if (read_text(path(RPROC "/state"), st, sizeof(st)))
		st[0] = '\0';
	if (!strcmp(val, "stop")) {
		if (strcmp(st, "running")) { errno = EINVAL; return -1; }
		res = "offline";
	} else if (!strcmp(val, "start")) {
		if (!strcmp(st, "running")) { errno = EBUSY; return -1; }
		if (read_text(path(RPROC "/firmware"), fw, sizeof(fw))) { errno = EIO; return -1; }
		snprintf(fwpath, sizeof(fwpath), "%s/%s", path(FW_DIR), fw);
		fd = open(fwpath, O_RDONLY);
		if (fd < 0) { errno = ENOENT; return -1; }
		n = read(fd, buf, sizeof(buf) - 1);
		close(fd);
		buf[n > 0 ? n : 0] = '\0';
		if (n > 0 && memmem(buf, n, "FAILSTART", 9)) { errno = EIO; return -1; }
		res = "running";
	} else {
		errno = EINVAL;
		return -1;
	}
	fd = open(file, O_WRONLY | O_TRUNC);
	if (fd < 0)
		return -1;
	n = write(fd, res, strlen(res));
	close(fd);
	return n < 0 ? -1 : 0;
}
#endif

static int write_sysfs(const char *file, const char *val)
{
	int fd, ret = 0;
	ssize_t n;

#ifdef RTOS_AGENT_TESTING
	if (strstr(file, "/state"))
		return fake_state_write(file, val);
	fd = open(file, O_WRONLY | O_CLOEXEC | O_TRUNC); /* plain files in tests */
#else
	fd = open(file, O_WRONLY | O_CLOEXEC);
#endif
	if (fd < 0)
		return -1;
	n = write(fd, val, strlen(val));
	if (n != (ssize_t)strlen(val))
		ret = -1;
	if (close(fd) && !ret)
		ret = -1;
	return ret;
}

/* ------------------------------------------------------------------ */
/* State                                                               */

static int rproc_present(void)
{
	struct stat st;
	return stat(path(RPROC "/state"), &st) == 0;
}

static const char *rproc_kernel_state(char *buf, size_t len)
{
	if (read_text(path(RPROC "/state"), buf, len))
		snprintf(buf, len, "missing");
	return buf;
}

/* PROMPT.md status vocabulary. */
static const char *state_word(const char *k)
{
	if (!strcmp(k, "running"))
		return "running";
	if (!strcmp(k, "offline"))
		return "stopped";
	return "faulted"; /* crashed, suspended, deleted, invalid, missing */
}

static int wait_running(int timeout_ms)
{
	char st[32];
	int waited = 0;

	for (;;) {
		if (!strcmp(rproc_kernel_state(st, sizeof(st)), "running"))
			return 0;
		if (waited >= timeout_ms)
			return -1;
		usleep(100 * 1000);
		waited += 100;
	}
}

/* stop (if running) -> firmware=<name> -> start -> wait for running */
static int rproc_restart(const char *fw_name, char *err, size_t errlen)
{
	char st[32];

	if (!rproc_present()) {
		snprintf(err, errlen, "%s does not exist", RPROC);
		return -1;
	}
	rproc_kernel_state(st, sizeof(st));
	if (!strcmp(st, "running") && write_sysfs(path(RPROC "/state"), "stop")) {
		snprintf(err, errlen, "stop: %s", strerror(errno));
		return -1;
	}
	/* The kernel refuses a firmware change unless the core is offline
	 * (remoteproc_sysfs.c firmware_store), hence stop first. */
	if (write_sysfs(path(RPROC "/firmware"), fw_name)) {
		snprintf(err, errlen, "firmware=%s: %s", fw_name, strerror(errno));
		return -1;
	}
	if (write_sysfs(path(RPROC "/state"), "start")) {
		snprintf(err, errlen, "start %s: %s", fw_name, strerror(errno));
		return -1;
	}
	if (wait_running(START_TIMEOUT_MS)) {
		snprintf(err, errlen, "%s did not reach running within %d ms", fw_name,
			 START_TIMEOUT_MS);
		return -1;
	}
	return 0;
}

/* ------------------------------------------------------------------ */
/* Lock and release                                                    */

static int lock_is_locked(void)
{
	char buf[32];

	if (read_text(path(LOCK_FILE), buf, sizeof(buf)))
		return 0; /* no file yet: factory state is unlocked */
	return strcmp(buf, "unlocked") != 0; /* anything unexpected counts as locked */
}

static int write_file_atomic(const char *file, const char *text)
{
	char tmp[600];
	int fd;

	snprintf(tmp, sizeof(tmp), "%s.tmp", file);
	fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
	if (fd < 0)
		return -1;
	if (write(fd, text, strlen(text)) != (ssize_t)strlen(text) || fsync(fd)) {
		close(fd);
		unlink(tmp);
		return -1;
	}
	close(fd);
	return rename(tmp, file);
}

static void release_version(char *out, size_t len)
{
	FILE *f = fopen(path(RELEASE_FILE), "r");
	char line[256];

	snprintf(out, len, "unknown");
	if (!f)
		return;
	while (fgets(line, sizeof(line), f)) {
		if (!strncmp(line, "DUO_PLAYGROUND_VERSION=", 23)) {
			line[strcspn(line, "\r\n")] = '\0';
			snprintf(out, len, "%.63s", line + 23);
			break;
		}
	}
	fclose(f);
}

static int require_root(const char *what)
{
#ifdef RTOS_AGENT_TESTING
	if (getenv("RTOS_AGENT_FAKE_ROOT"))
		return 0;
#endif
	if (geteuid() != 0) {
		fprintf(stderr, "%s: %s must run as root\n", prog, what);
		return -1;
	}
	return 0;
}

static int take_run_lock(void)
{
	int fd = open(path(RUN_LOCK), O_RDWR | O_CREAT | O_CLOEXEC, 0600);

	if (fd < 0 || flock(fd, LOCK_EX))
		return -1;
	return fd; /* released at exit */
}

/* ------------------------------------------------------------------ */
/* Carveout from the live device tree, with the build-time fallback     */

static void elf_rules(struct elf_rules *r)
{
	glob_t g;

	r->boot_addr = PG_C906L_BOOT_ADDR;
	r->region_base = PG_RTOS_BASE;
	r->region_size = PG_RTOS_SIZE;
	if (glob(path(DT_CARVEOUT_GLOB), 0, NULL, &g) == 0) {
		uint8_t c[16];
		int fd = open(g.gl_pathv[0], O_RDONLY);

		/* reg = <addr_hi addr_lo size_hi size_lo>, big endian */
		if (fd >= 0 && read(fd, c, 16) == 16) {
			uint64_t v[2];
			int i, j;

			for (i = 0; i < 2; i++)
				for (v[i] = 0, j = 0; j < 8; j++)
					v[i] = v[i] << 8 | c[8 * i + j];
			r->region_base = v[0];
			r->region_size = v[1];
		}
		if (fd >= 0)
			close(fd);
		globfree(&g);
	}
}

/* ------------------------------------------------------------------ */
/* Commands                                                            */

static int cmd_status(void)
{
	char st[32], fw[256], ver[64];
	int present = rproc_present();

	rproc_kernel_state(st, sizeof(st));
	if (!present || read_text(path(RPROC "/firmware"), fw, sizeof(fw)))
		snprintf(fw, sizeof(fw), "none");
	release_version(ver, sizeof(ver));
	printf("state=%s\n", state_word(st));
	if (strcmp(fw, "none"))
		printf("firmware=%s/%s\n", FW_DIR, fw);
	else
		printf("firmware=none\n");
	printf("lock=%s\n", lock_is_locked() ? "locked" : "unlocked");
	printf("release=%s\n", ver);
	if (!present) {
		fprintf(stderr, "%s: %s does not exist\n", prog, RPROC);
		return EXIT_ERR;
	}
	return EXIT_OK;
}

static int do_recover(const char *why)
{
	char err[256];

	if (rproc_restart(STARTER_NAME, err, sizeof(err))) {
		fprintf(stderr, "%s: recover failed: %s\n", prog, err);
		logmsg(LOG_ERR, "recover reason=%s result=failed error=%s", why, err);
		return EXIT_NOSTART;
	}
	printf("recovered %s/%s state=running\n", FW_DIR, STARTER_NAME);
	logmsg(LOG_NOTICE, "recover reason=%s result=ok firmware=%s", why, STARTER_NAME);
	return EXIT_OK;
}

static int cmd_load(const char *src)
{
	char dst[512], tmp[520], err[256], why[256], hex[65];
	struct elf_rules rules;
	struct stat st;
	uint8_t *data;
	int fd, dfd;
	ssize_t n;

	if (require_root("load"))
		return EXIT_ERR;
	if (lock_is_locked()) {
		printf("locked\n");
		logmsg(LOG_NOTICE, "load file=%s sha256=- result=locked", src);
		return EXIT_LOCKED;
	}
	if (take_run_lock() < 0) {
		fprintf(stderr, "%s: cannot take %s: %s\n", prog, RUN_LOCK, strerror(errno));
		return EXIT_ERR;
	}

	fd = open(src, O_RDONLY | O_CLOEXEC);
	if (fd < 0 || fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_size > MAX_ELF) {
		printf("bad file: %s: %s\n", src, fd < 0 ? strerror(errno) : "not a regular file under 32 MiB");
		logmsg(LOG_NOTICE, "load file=%s sha256=- result=bad-file", src);
		return EXIT_BADFILE;
	}
	data = malloc(st.st_size ? st.st_size : 1);
	if (!data || (n = read(fd, data, st.st_size)) != st.st_size) {
		fprintf(stderr, "%s: read %s failed\n", prog, src);
		return EXIT_ERR;
	}
	close(fd);
	sha256_hex(data, n, hex);

	elf_rules(&rules);
	if (elf_check(data, n, &rules, why, sizeof(why))) {
		printf("bad file: %s: %s\n", src, why);
		logmsg(LOG_NOTICE, "load file=%s sha256=%s result=bad-file reason=%s", src, hex, why);
		return EXIT_BADFILE;
	}

	/* Copy next to the live firmware, fsync, then rename onto it. The
	 * starter copy is never touched. */
	snprintf(dst, sizeof(dst), "%s/%s", path(FW_DIR), FW_NAME);
	snprintf(tmp, sizeof(tmp), "%s.new", dst);
	fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
	if (fd < 0 || write(fd, data, n) != n || fsync(fd) || close(fd)) {
		fprintf(stderr, "%s: write %s: %s\n", prog, tmp, strerror(errno));
		unlink(tmp);
		return EXIT_ERR;
	}
	free(data);
	if (rename(tmp, dst)) {
		fprintf(stderr, "%s: rename %s: %s\n", prog, tmp, strerror(errno));
		unlink(tmp);
		return EXIT_ERR;
	}
	dfd = open(path(FW_DIR), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	if (dfd >= 0) {
		fsync(dfd);
		close(dfd);
	}

	if (rproc_restart(FW_NAME, err, sizeof(err))) {
		printf("start failed: %s\n", err);
		logmsg(LOG_ERR, "load file=%s sha256=%s result=start-failed error=%s", src, hex, err);
		do_recover("load-failed");
		return EXIT_NOSTART;
	}
	printf("loaded %s/%s sha256=%s state=running\n", FW_DIR, FW_NAME, hex);
	logmsg(LOG_NOTICE, "load file=%s sha256=%s result=ok", src, hex);
	return EXIT_OK;
}

static int cmd_recover(void)
{
	if (require_root("recover"))
		return EXIT_ERR;
	if (lock_is_locked()) {
		/* recover restarts the core too; same rule as load */
		printf("locked\n");
		return EXIT_LOCKED;
	}
	if (take_run_lock() < 0)
		return EXIT_ERR;
	return do_recover("user");
}

static int cmd_set_lock(int locked)
{
	char dir[512];

	if (require_root(locked ? "lock" : "unlock"))
		return EXIT_ERR;
	snprintf(dir, sizeof(dir), "%s", path("/etc/rtos-agent"));
	mkdir(dir, 0755);
	if (write_file_atomic(path(LOCK_FILE), locked ? "locked\n" : "unlocked\n")) {
		fprintf(stderr, "%s: write %s: %s\n", prog, LOCK_FILE, strerror(errno));
		return EXIT_ERR;
	}
	printf("%s\n", locked ? "locked" : "unlocked");
	logmsg(LOG_NOTICE, "%s", locked ? "lock" : "unlock");
	return EXIT_OK;
}

/* rtos-agent.service: set the lock default, confirm remoteproc0, make
 * sure some program is running. Exits; nothing stays resident. */
static int cmd_boot(void)
{
	char st[32], err[256], dir[512];
	struct stat sb;

	snprintf(dir, sizeof(dir), "%s", path("/etc/rtos-agent"));
	mkdir(dir, 0755);
	if (stat(path(LOCK_FILE), &sb) && write_file_atomic(path(LOCK_FILE), "unlocked\n"))
		logmsg(LOG_WARNING, "cannot write %s: %s", LOCK_FILE, strerror(errno));

	if (!rproc_present()) {
		logmsg(LOG_ERR, "%s missing: the C906L remoteproc driver did not probe; "
		       "uploads are unavailable", RPROC);
		return EXIT_ERR;
	}
	/* cvitek_remoteproc.ko auto-boots firmware-name (playground.elf) as
	 * soon as it probes; give it time. */
	if (wait_running(BOOT_TIMEOUT_MS) == 0) {
		logmsg(LOG_INFO, "boot state=running firmware=%s",
		       read_text(path(RPROC "/firmware"), err, sizeof(err)) ? "?" : err);
		return EXIT_OK;
	}
	logmsg(LOG_WARNING, "boot: state=%s after %d ms; starting %s",
	       rproc_kernel_state(st, sizeof(st)), BOOT_TIMEOUT_MS, FW_NAME);
	if (rproc_restart(FW_NAME, err, sizeof(err)) == 0)
		return EXIT_OK;
	logmsg(LOG_ERR, "boot: %s", err);
	return do_recover("boot") == EXIT_OK ? EXIT_OK : EXIT_ERR;
}

/* ---- log ---- */

static void log_unavailable(const char *why)
{
	printf("rtos-agent: no C906 log buffer available (%s).\n", why);
	printf("The remoteproc driver of this build has no trace buffer. The C906 prints\n"
	       "its lines with the \"RT: \" prefix on %s, the Linux serial console\n"
	       "(J22, 115200 8N1). Read them there.\n", CONSOLE_UART);
}

/* Read the vendor dump ring through /dev/cvi-fast-image. Returns the
 * linear buffer (oldest first) in buf and len, and the header in h. */
static int dump_read(int fd, struct dump_uart_s *h, char **buf, size_t *len)
{
	size_t total;
	char *b;

	memset(h, 0, sizeof(*h));
	if (ioctl(fd, FAST_IMAGE_QUERY_DUMP_MSG_INFO, h) < 0)
		return -1;
	/* The driver returns 0 without copying when the RTOS did not answer
	 * or the ring is disabled; the zeroed header tells us. */
	if (h->dump_uart_max_size == 0 || !h->dump_uart_enable)
		return -1;
	total = sizeof(struct dump_uart_s) + h->dump_uart_max_size;
	b = calloc(1, total);
	if (!b)
		return -1;
	if (ioctl(fd, FAST_IMAGE_QUERY_DUMP_MSG, b) < 0) {
		free(b);
		return -1;
	}
	*buf = b;
	*len = h->dump_uart_overflow ? h->dump_uart_max_size : h->dump_uart_pos;
	return 0;
}

static void put_clean(const char *p, size_t n)
{
	size_t i;

	for (i = 0; i < n; i++)
		if (p[i] != '\r' && p[i] != '\0')
			putchar(p[i]);
	fflush(stdout);
}

static int cmd_log(int follow)
{
	struct dump_uart_s h, last;
	size_t len, newbytes;
	char *buf;
	int fd, first = 1;

	fd = open(path(FAST_IMAGE_DEV), O_RDWR | O_CLOEXEC);
	if (fd < 0) {
		log_unavailable(FAST_IMAGE_DEV " cannot be opened");
		return EXIT_ERR;
	}
	memset(&last, 0, sizeof(last));
	signal(SIGPIPE, SIG_DFL);
	for (;;) {
		if (dump_read(fd, &h, &buf, &len)) {
			if (first) {
				log_unavailable("the C906 program did not answer SYS_CMD_INFO_DUMP_MSG");
				return EXIT_ERR;
			}
			sleep(1);
			continue;
		}
		if (first || h.dump_uart_ptr != last.dump_uart_ptr) {
			if (!first)
				printf("--- C906 program restarted ---\n");
			put_clean(buf + sizeof(struct dump_uart_s), len);
		} else {
			newbytes = (h.dump_uart_pos + h.dump_uart_max_size - last.dump_uart_pos) %
				   h.dump_uart_max_size;
			if (newbytes > len)
				newbytes = len;
			put_clean(buf + sizeof(struct dump_uart_s) + len - newbytes, newbytes);
		}
		free(buf);
		last = h;
		first = 0;
		if (!follow)
			return EXIT_OK;
		/* The SSH session went away (we were re-parented): stop polling
		 * the C906 instead of lingering until the next write fails. */
		if (getppid() == 1)
			return EXIT_OK;
		usleep(500 * 1000);
	}
}

static int usage(void)
{
	fprintf(stderr,
		"usage: %s status | load <elf> | recover | log [-f] | lock | unlock\n", prog);
	return EXIT_ERR;
}

int main(int argc, char **argv)
{
	const char *cmd;

	openlog("rtos-agent", LOG_PID, LOG_DAEMON);
	setvbuf(stdout, NULL, _IOLBF, 0);
	if (argc < 2)
		return usage();
	cmd = argv[1];
	if (!strcmp(cmd, "status") && argc == 2)
		return cmd_status();
	if (!strcmp(cmd, "load") && argc == 3)
		return cmd_load(argv[2]);
	if (!strcmp(cmd, "recover") && argc == 2)
		return cmd_recover();
	if (!strcmp(cmd, "log") && (argc == 2 || (argc == 3 && !strcmp(argv[2], "-f"))))
		return cmd_log(argc == 3);
	if (!strcmp(cmd, "lock") && argc == 2)
		return cmd_set_lock(1);
	if (!strcmp(cmd, "unlock") && argc == 2)
		return cmd_set_lock(0);
	if (!strcmp(cmd, "boot") && argc == 2)
		return cmd_boot();
	return usage();
}
