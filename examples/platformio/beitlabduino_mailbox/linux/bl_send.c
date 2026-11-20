// SPDX-License-Identifier: MIT
/*
 * bl_send: send one beitlabduino command to the C906L through
 * /dev/cvi-rtos-cmdqu and print the reply. Runs on the Duo S (Linux).
 * Built and installed as /usr/bin/bl_send by install.py in this folder.
 *
 *   bl_send <cmd_id> [value] [timeout_ms]   default value 0, timeout 1000
 *
 * cmd_id 0x00..0x4f, not 0x40 (that is ping: use host_ping). value
 * 0..0x7fffffff. Numbers may be decimal or 0x hex.
 *
 * Exit: 0 reply printed; 1 usage/device error; 2 no reply (timeout) or a
 *       reply without the ack bit; 3 echoed unchanged (the running C906
 *       program does not listen for cmd_id); 4 refused (BL_CMD_ERROR).
 *
 * Reply convention: common/lib/beitlabduino/beitlabduino.h.
 */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include "../../../../firmware/include/playground_cmd.h"

/* Same values as beitlabduino.h. */
#define BL_CMD_ACK   0x80000000u
#define BL_CMD_ERROR 0x7fffffffu

/* ---- Copied unchanged from the pinned SDK (sdk.lock sdk_commit):
 * freertos/cvitek/driver/rtos_cmdqu/include/rtos_cmdqu.h
 * (sha256 0c161ca04037a9905f7655e073f593526fec22d5111e8b7a821fa18ebadac9e2,
 * recorded in sdk.lock as cmdqu_header_sha256), as in host_ping.c. ---- */
#define NR_SYSTEM_CMD           20

enum IP_TYPE {
	IP_ISP = 0,
	IP_VCODEC,
	IP_VIP,
	IP_VI,
	IP_RGN,
	IP_AUDIO,
	IP_SYSTEM,
	IP_CAMERA,
	IP_LIMIT,
};
struct valid_t {
	unsigned char linux_valid;
	unsigned char rtos_valid;
} __attribute__((packed));

typedef union resv_t {
	struct valid_t valid;
	unsigned short mstime; // 0 : noblock, -1 : block infinite
} resv_t;

typedef struct cmdqu_t cmdqu_t;
/* cmdqu size should be 8 bytes because of mailbox buffer size */
struct cmdqu_t {
	unsigned char ip_id;
	unsigned char cmd_id : 7;
	unsigned char block : 1;
	union resv_t resv;
	unsigned int  param_ptr;
} __attribute__((packed)) __attribute__((aligned(0x8)));

/* keep those commands for ioctl system used */
enum SYSTEM_CMD_TYPE {
	CMDQU_SEND = 1,
	CMDQU_REQUEST,
	CMDQU_REQUEST_FREE,
	CMDQU_SEND_WAIT,
	CMDQU_SEND_WAKEUP,
	CMDQU_SYSTEM_LIMIT = NR_SYSTEM_CMD,
};

#define RTOS_CMDQU_DEV_NAME "cvi-rtos-cmdqu"
#define RTOS_CMDQU_SEND                         _IOW('r', CMDQU_SEND, unsigned long)
#define RTOS_CMDQU_REQUEST                      _IOW('r', CMDQU_REQUEST, unsigned long)
#define RTOS_CMDQU_REQUEST_FREE                 _IOW('r', CMDQU_REQUEST_FREE, unsigned long)
#define RTOS_CMDQU_SEND_WAIT                    _IOW('r', CMDQU_SEND_WAIT, unsigned long)
#define RTOS_CMDQU_SEND_WAKEUP                  _IOW('r', CMDQU_SEND_WAKEUP, unsigned long)
/* ---- end of copy ---- */

_Static_assert(sizeof(cmdqu_t) == 8, "cmdqu_t must be 8 bytes (mailbox slot)");
_Static_assert(PLAYGROUND_IP_ID == IP_SYSTEM, "playground_cmd.h IP id drifted");

static int number(const char *s, unsigned long max, unsigned long *out)
{
	char *end;

	errno = 0;
	*out = strtoul(s, &end, 0);
	return errno || *s == '-' || *end || *out > max ? -1 : 0;
}

int main(int argc, char **argv)
{
	unsigned long id, value = 0, timeout = PLAYGROUND_PING_TIMEOUT_MS;
	cmdqu_t cmd;
	int fd;

	if (argc < 2 || argc > 4 || number(argv[1], 0x7f, &id) ||
	    (argc > 2 && number(argv[2], 0x7fffffff, &value)) ||
	    (argc > 3 && number(argv[3], 60000, &timeout))) {
		fprintf(stderr, "usage: bl_send <cmd_id 0x00..0x4f> [value 0..0x7fffffff] "
			"[timeout_ms 1..60000]\n");
		return 1;
	}
	if (id >= PLAYGROUND_VENDOR_SYS_FIRST || id == PLAYGROUND_CMD_PING) {
		fprintf(stderr, "bl_send: cmd 0x%02lx: use 0x00..0x4f, not 0x%02x (ping: host_ping)\n",
			id, PLAYGROUND_CMD_PING);
		return 1;
	}
	/* resv is a union: the request carries mstime, a C906 reply carries
	 * valid = {1, 1}, which reads as mstime 0x0101 (see host_ping.c). */
	if (timeout == 0 || timeout == 0x0101) {
		fprintf(stderr, "bl_send: timeout must be 1..60000 ms and not 257\n");
		return 1;
	}
	fd = open("/dev/" RTOS_CMDQU_DEV_NAME, O_RDWR);
	if (fd < 0) {
		fprintf(stderr, "bl_send: /dev/%s: %s\n", RTOS_CMDQU_DEV_NAME, strerror(errno));
		return 1;
	}
	memset(&cmd, 0, sizeof(cmd));
	cmd.ip_id = IP_SYSTEM;
	cmd.cmd_id = (unsigned char)id;
	cmd.block = 1;
	cmd.resv.mstime = (unsigned short)timeout;
	cmd.param_ptr = (unsigned int)value;

	/* SEND_WAIT returns 0 even on timeout and then leaves the request
	 * unchanged, so the reply is judged by its content. */
	if (ioctl(fd, RTOS_CMDQU_SEND_WAIT, &cmd) < 0) {
		fprintf(stderr, "bl_send: RTOS_CMDQU_SEND_WAIT: %s\n", strerror(errno));
		close(fd);
		return 1;
	}
	close(fd);
	if (!(cmd.resv.valid.linux_valid == 1 && cmd.resv.valid.rtos_valid == 1)) {
		printf("bl_send: cmd 0x%02lx: no reply within %lu ms\n", id, timeout);
		return 2;
	}
	if (cmd.param_ptr == (unsigned int)value) {
		printf("bl_send: cmd 0x%02lx: echoed unchanged: the running C906 program "
		       "does not listen for it\n", id);
		return 3;
	}
	if (!(cmd.param_ptr & BL_CMD_ACK)) {
		printf("bl_send: cmd 0x%02lx: unexpected reply 0x%08x (no ack bit)\n",
		       id, cmd.param_ptr);
		return 2;
	}
	if ((cmd.param_ptr & ~BL_CMD_ACK) == BL_CMD_ERROR) {
		printf("bl_send: cmd 0x%02lx value %lu: refused\n", id, value);
		return 4;
	}
	printf("%u\n", cmd.param_ptr & ~BL_CMD_ACK);
	return 0;
}
