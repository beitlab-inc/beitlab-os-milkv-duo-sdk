# SPDX-License-Identifier: MIT
#
# rtos-agent
#
#   make MEMMAP_H=<sdk>/build/output/<board>/cvi_board_memmap.h \
#        CROSS_COMPILE=<aarch64 toolchain prefix>
#       -> build/rtos-agent (static, for the Duo S)
#   make test
#       -> host build with -DRTOS_AGENT_TESTING, runs tests/
#
# The C906L addresses are never written here: they are read from the board
# memmap the SDK generates (cvi_board_memmap.h). scripts/build-image.sh
# passes MEMMAP_H. The test target falls back to the in-tree sdk/ checkout.

CROSS_COMPILE ?=
CC := $(CROSS_COMPILE)gcc
CFLAGS ?= -O2
CFLAGS += -std=gnu11 -Wall -Wextra -Wno-unused-result
REPO := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
BOARD := $(shell sed -n 's/^board_link[[:space:]]*=[[:space:]]*//p' $(REPO)/sdk.lock)
BEITLAB_WORK_DIR ?= $(if $(XDG_CACHE_HOME),$(XDG_CACHE_HOME),$(HOME)/.cache)/beitlab-duo-os
PLAYGROUND_SDK ?= $(BEITLAB_WORK_DIR)/sdk
MEMMAP_H ?= $(PLAYGROUND_SDK)/build/output/$(BOARD)/cvi_board_memmap.h

memmap = $(shell sed -n 's/^\#define $(1) \(0x[0-9a-fA-F]*\).*/\1/p' $(MEMMAP_H) 2>/dev/null)
PG_C906L_BOOT_ADDR ?= $(call memmap,CVIMMAP_FSBL_C906L_START_ADDR)
PG_RTOS_BASE ?= $(call memmap,CVIMMAP_FREERTOS_ADDR)
PG_RTOS_SIZE ?= $(call memmap,CVIMMAP_FREERTOS_SIZE)

SRCS := rtos-agent.c elfcheck.c sha256.c
HDRS := elfcheck.h sha256.h vendor_abi.h
B ?= $(BEITLAB_WORK_DIR)/build/agent

define need
$(if $($(1)),,$(error $(1) is empty: pass MEMMAP_H=<sdk>/build/output/$(BOARD)/cvi_board_memmap.h, or run scripts/fetch-freertos.sh))
endef

DEFS = -DPG_C906L_BOOT_ADDR=$(PG_C906L_BOOT_ADDR)ull -DPG_RTOS_BASE=$(PG_RTOS_BASE)ull \
       -DPG_RTOS_SIZE=$(PG_RTOS_SIZE)ull

all: $(B)/rtos-agent

$(B)/rtos-agent: $(SRCS) $(HDRS)
	$(call need,PG_C906L_BOOT_ADDR)$(call need,PG_RTOS_BASE)$(call need,PG_RTOS_SIZE)
	@mkdir -p $(B)
	$(CC) $(CFLAGS) $(DEFS) -static -o $@ $(SRCS)

$(B)/rtos-agent-test: $(SRCS) $(HDRS)
	$(call need,PG_C906L_BOOT_ADDR)$(call need,PG_RTOS_BASE)$(call need,PG_RTOS_SIZE)
	@mkdir -p $(B)
	gcc $(CFLAGS) -g $(DEFS) -DRTOS_AGENT_TESTING -o $@ $(SRCS)

$(B)/test_unit: tests/test_unit.c elfcheck.c sha256.c $(HDRS)
	@mkdir -p $(B)
	gcc $(CFLAGS) -g -I. -o $@ tests/test_unit.c elfcheck.c sha256.c

test: $(B)/rtos-agent-test $(B)/test_unit
	$(B)/test_unit
	PG_RTOS_BASE=$(PG_RTOS_BASE) PG_RTOS_SIZE=$(PG_RTOS_SIZE) \
	PG_C906L_BOOT_ADDR=$(PG_C906L_BOOT_ADDR) \
	  sh tests/test_cli.sh $(abspath $(B)/rtos-agent-test)

clean:
	rm -rf $(B)

.PHONY: all test clean
