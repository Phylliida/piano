# Firmware for the tile and the hub.
#
#   make test    build core/ and sim/ for this PC and run every test
#   make tile    build/tile/tile.elf, .bin, .map      (needs tools/fetch_deps.sh)
#   make hub     build/hub/hub.elf, .bin, .map        (needs tools/fetch_deps.sh)
#   make         all three
#
# CROSS is the ARM toolchain's prefix: make tile CROSS=/path/to/bin/arm-none-eabi-

CROSS  ?= arm-none-eabi-
HOSTCC ?= cc
B      := build

.PHONY: all test tile hub clean
all: test tile hub

clean:
	rm -rf $(B)

# ------------------------------------------------------------------ tests ---
# Everything in core/ and sim/ goes into every test program; a test is one
# file, tests/test_*.c.

CORE_SRC := $(wildcard core/*.c)
SIM_SRC  := $(wildcard sim/*.c)
TESTS    := $(patsubst tests/%.c,%,$(wildcard tests/test_*.c))

HOST_CFLAGS := -std=c11 -O1 -g -Wall -Wextra -Werror \
               -fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer \
               -Icore -Isim -Itests -MMD -MP
HOST_OBJ := $(patsubst %.c,$(B)/host/%.o,$(CORE_SRC) $(SIM_SRC))

$(B)/host/%.o: %.c
	@mkdir -p $(dir $@)
	@echo "  CC   $<"
	@$(HOSTCC) $(HOST_CFLAGS) -c $< -o $@

$(B)/host/%: $(B)/host/tests/%.o $(HOST_OBJ)
	@echo "  LINK $@"
	@$(HOSTCC) $(HOST_CFLAGS) $^ -lm -o $@

test: $(TESTS:%=$(B)/host/%)
	@fail=0; for t in $^; do ./$$t || fail=1; done; \
	if [ $$fail = 0 ]; then echo "all tests passed"; else echo "TESTS FAILED"; exit 1; fi

.PRECIOUS: $(B)/host/%.o
-include $(HOST_OBJ:.o=.d) $(TESTS:%=$(B)/host/tests/%.d)

# ------------------------------------------------------------- ARM builds ---
# Our own files are compiled with -Wall -Wextra -Werror. The two libraries
# in deps/ are compiled as they come, by path, with their warnings left
# alone; nothing of theirs is ever run.

A   := deps/artery/libraries
USB := deps/tinyusb/src

ARM_CPU    := -mcpu=cortex-m4 -mthumb -mfloat-abi=soft
ARM_CFLAGS := $(ARM_CPU) -std=c11 -Os -g -ffunction-sections -fdata-sections -fno-common \
              -DAT32F405RCT7 -Icore -Iport/at32f405 \
              -isystem $(A)/cmsis/cm4/core_support -isystem $(A)/cmsis/cm4/device_support \
              -isystem $(A)/drivers/inc -MMD -MP
ARM_WARN   := -Wall -Wextra -Werror
ARM_LDFLAGS := $(ARM_CPU) -T port/at32f405/at32f405.ld -nostartfiles \
               --specs=nano.specs --specs=nosys.specs -Wl,--gc-sections

ARM_STARTUP := $(A)/cmsis/cm4/device_support/startup/gcc/startup_at32f402_405.s
ARTERY_SRC  := $(A)/cmsis/cm4/device_support/system_at32f402_405.c \
               $(patsubst %,$(A)/drivers/src/at32f402_405_%.c,adc crm dma flash gpio misc pwc tmr usart)
PORT_SRC    := port/at32f405/port.c port/at32f405/uart.c port/at32f405/flash.c

TILE_SRC := $(CORE_SRC) $(PORT_SRC) tile/main.c $(ARTERY_SRC)
HUB_SRC  := $(CORE_SRC) $(PORT_SRC) port/at32f405/i2c.c hub/main.c hub/usb.c $(ARTERY_SRC) \
            $(A)/drivers/src/at32f402_405_i2c.c \
            $(patsubst %,$(USB)/%.c,tusb common/tusb_fifo device/usbd class/cdc/cdc_device \
              class/midi/midi_device portable/synopsys/dwc2/dcd_dwc2 portable/synopsys/dwc2/dwc2_common)

TILE_OBJ := $(TILE_SRC:%.c=$(B)/tile/%.o) $(ARM_STARTUP:%.s=$(B)/tile/%.o)
HUB_OBJ  := $(HUB_SRC:%.c=$(B)/hub/%.o) $(ARM_STARTUP:%.s=$(B)/hub/%.o)

TILE_CFLAGS := -Itile
HUB_CFLAGS  := -Ihub -isystem $(USB) -DCFG_TUSB_MCU=OPT_MCU_AT32F402_405

.PHONY: have-deps
have-deps:
	@test -d $(A) -a -d $(USB) || \
	{ echo "error: firmware/deps is missing; run: bash tools/fetch_deps.sh"; exit 1; }

$(B)/tile/deps/%.o: ARM_WARN :=
$(B)/hub/deps/%.o: ARM_WARN :=

$(B)/tile/%.o: %.c | have-deps
	@mkdir -p $(dir $@)
	@echo "  CC   $<"
	@$(CROSS)gcc $(ARM_CFLAGS) $(TILE_CFLAGS) $(ARM_WARN) -c $< -o $@

$(B)/hub/%.o: %.c | have-deps
	@mkdir -p $(dir $@)
	@echo "  CC   $<"
	@$(CROSS)gcc $(ARM_CFLAGS) $(HUB_CFLAGS) $(ARM_WARN) -c $< -o $@

$(B)/tile/%.o: %.s | have-deps
	@mkdir -p $(dir $@)
	@echo "  AS   $<"
	@$(CROSS)gcc $(ARM_CPU) -c $< -o $@

$(B)/hub/%.o: %.s | have-deps
	@mkdir -p $(dir $@)
	@echo "  AS   $<"
	@$(CROSS)gcc $(ARM_CPU) -c $< -o $@

$(B)/tile/tile.elf: $(TILE_OBJ) port/at32f405/at32f405.ld
	@echo "  LINK $@"
	@$(CROSS)gcc $(ARM_LDFLAGS) -Wl,-Map=$(B)/tile/tile.map $(TILE_OBJ) -o $@

$(B)/hub/hub.elf: $(HUB_OBJ) port/at32f405/at32f405.ld
	@echo "  LINK $@"
	@$(CROSS)gcc $(ARM_LDFLAGS) -Wl,-Map=$(B)/hub/hub.map $(HUB_OBJ) -o $@

%.bin: %.elf
	@$(CROSS)objcopy -O binary $< $@

tile: $(B)/tile/tile.bin
	@$(CROSS)size $(B)/tile/tile.elf

hub: $(B)/hub/hub.bin
	@$(CROSS)size $(B)/hub/hub.elf

-include $(TILE_OBJ:.o=.d) $(HUB_OBJ:.o=.d)
