# Firmware for the tile and the hub.
#
#   make test         build core/ and sim/ for this PC and run every test
#   make test-tools   test the scripts in tools/ (needs python3; not part of `make`)
#   make boot         build/boot/boot.bin: the bootloader, the first 8 KB of every chip
#   make tile         build/tile/tile_a.img, tile_b.img: the tile's program for either
#                     slot, as tools/update.py sends it through the hub;
#                     build/tile/tile_factory.bin: bootloader and program, for a
#                     chip's first flashing (tools/flash_tile.py)
#   make hub          the same for the hub: build/hub/hub_a.img, hub_b.img,
#                     hub_factory.bin (first flashing: USB DFU)
#   make              test, tile and hub
#
# boot, tile and hub need tools/fetch_deps.sh to have run, and python3.
# CROSS is the ARM toolchain's prefix: make tile CROSS=/path/to/bin/arm-none-eabi-

CROSS  ?= arm-none-eabi-
HOSTCC ?= cc
B      := build

.PHONY: all test test-tools boot tile hub clean
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

# The scripts are tested against tests/hubsim.c, which is the hub's and the
# tiles' programs behind a pipe, and against an imitation of the hub in
# Python; what they know of an image, against core/fw.c behind another pipe
# (tests/fwcheck.c).
test-tools: $(B)/host/hubsim $(B)/host/fwcheck
	@python3 tests/test_mkimage.py
	@python3 tests/test_flash_tile.py
	@python3 tests/test_update.py

.PRECIOUS: $(B)/host/%.o
-include $(HOST_OBJ:.o=.d) $(TESTS:%=$(B)/host/tests/%.d) \
         $(B)/host/tests/hubsim.d $(B)/host/tests/fwcheck.d

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
ARM_LDFLAGS := $(ARM_CPU) -L port/at32f405 -nostartfiles \
               --specs=nano.specs --specs=nosys.specs -Wl,--gc-sections
LD := port/at32f405

ARM_STARTUP := $(A)/cmsis/cm4/device_support/startup/gcc/startup_at32f402_405.s
ARTERY_SRC  := $(A)/cmsis/cm4/device_support/system_at32f402_405.c \
               $(patsubst %,$(A)/drivers/src/at32f402_405_%.c,adc crm dma flash gpio misc pwc tmr usart wdt)
PORT_SRC    := port/at32f405/port.c port/at32f405/uart.c port/at32f405/flash.c

# The bootloader: no port.c, no pins, only what it takes to choose a slot.
BOOT_SRC := boot/main.c core/fw.c core/util.c port/at32f405/flash.c \
            $(A)/cmsis/cm4/device_support/system_at32f402_405.c \
            $(patsubst %,$(A)/drivers/src/at32f402_405_%.c,crm flash wdt)

TILE_SRC := $(CORE_SRC) $(PORT_SRC) tile/main.c $(ARTERY_SRC)
HUB_SRC  := $(CORE_SRC) $(PORT_SRC) port/at32f405/i2c.c hub/main.c hub/usb.c $(ARTERY_SRC) \
            $(A)/drivers/src/at32f402_405_i2c.c \
            $(patsubst %,$(USB)/%.c,tusb common/tusb_fifo device/usbd class/cdc/cdc_device \
              class/midi/midi_device portable/synopsys/dwc2/dcd_dwc2 portable/synopsys/dwc2/dwc2_common)

BOOT_OBJ := $(BOOT_SRC:%.c=$(B)/boot/%.o) $(ARM_STARTUP:%.s=$(B)/boot/%.o)
TILE_OBJ := $(TILE_SRC:%.c=$(B)/tile/%.o) $(ARM_STARTUP:%.s=$(B)/tile/%.o)
HUB_OBJ  := $(HUB_SRC:%.c=$(B)/hub/%.o) $(ARM_STARTUP:%.s=$(B)/hub/%.o)

TILE_CFLAGS := -Itile
HUB_CFLAGS  := -Ihub -isystem $(USB) -DCFG_TUSB_MCU=OPT_MCU_AT32F402_405

.PHONY: have-deps
have-deps:
	@test -d $(A) -a -d $(USB) || \
	{ echo "error: firmware/deps is missing; run: bash tools/fetch_deps.sh"; exit 1; }

$(B)/boot/deps/%.o: ARM_WARN :=
$(B)/tile/deps/%.o: ARM_WARN :=
$(B)/hub/deps/%.o: ARM_WARN :=

$(B)/boot/%.o: %.c | have-deps
	@mkdir -p $(dir $@)
	@echo "  CC   $<"
	@$(CROSS)gcc $(ARM_CFLAGS) $(ARM_WARN) -c $< -o $@

$(B)/tile/%.o: %.c | have-deps
	@mkdir -p $(dir $@)
	@echo "  CC   $<"
	@$(CROSS)gcc $(ARM_CFLAGS) $(TILE_CFLAGS) $(ARM_WARN) -c $< -o $@

$(B)/hub/%.o: %.c | have-deps
	@mkdir -p $(dir $@)
	@echo "  CC   $<"
	@$(CROSS)gcc $(ARM_CFLAGS) $(HUB_CFLAGS) $(ARM_WARN) -c $< -o $@

$(B)/boot/%.o: %.s | have-deps
	@mkdir -p $(dir $@)
	@echo "  AS   $<"
	@$(CROSS)gcc $(ARM_CPU) -c $< -o $@

$(B)/tile/%.o: %.s | have-deps
	@mkdir -p $(dir $@)
	@echo "  AS   $<"
	@$(CROSS)gcc $(ARM_CPU) -c $< -o $@

$(B)/hub/%.o: %.s | have-deps
	@mkdir -p $(dir $@)
	@echo "  AS   $<"
	@$(CROSS)gcc $(ARM_CPU) -c $< -o $@

# The bootloader is linked for the start of flash; the link fails if it
# outgrows its 8 KB. A program is compiled once and linked twice, for slot A
# and for slot B (core/fw.h): it runs only at the address it was linked for.
$(B)/boot/boot.elf: $(BOOT_OBJ) $(LD)/boot.ld $(LD)/sections.ld
	@echo "  LINK $@"
	@$(CROSS)gcc $(ARM_LDFLAGS) -T $(LD)/boot.ld -Wl,-Map=$(B)/boot/boot.map $(BOOT_OBJ) -o $@

$(B)/tile/tile_%.elf: $(TILE_OBJ) $(LD)/slot_%.ld $(LD)/sections.ld
	@echo "  LINK $@"
	@$(CROSS)gcc $(ARM_LDFLAGS) -T $(LD)/slot_$*.ld -Wl,-Map=$(B)/tile/tile_$*.map $(TILE_OBJ) -o $@

$(B)/hub/hub_%.elf: $(HUB_OBJ) $(LD)/slot_%.ld $(LD)/sections.ld
	@echo "  LINK $@"
	@$(CROSS)gcc $(ARM_LDFLAGS) -T $(LD)/slot_$*.ld -Wl,-Map=$(B)/hub/hub_$*.map $(HUB_OBJ) -o $@

%.bin: %.elf
	@$(CROSS)objcopy -O binary $< $@

# An image is the program with its length filled in and its checksum behind
# it. A factory file is a whole chip from 0x08000000 up to the settings:
# the bootloader, the image for slot A, and nothing in slot B.
%.img: %.bin tools/mkimage.py
	@echo "  IMG  $@"
	@python3 tools/mkimage.py image $< $@

$(B)/tile/tile_factory.bin: $(B)/boot/boot.bin $(B)/tile/tile_a.img tools/mkimage.py
	@echo "  IMG  $@"
	@python3 tools/mkimage.py factory $(B)/boot/boot.bin $(B)/tile/tile_a.img $@

$(B)/hub/hub_factory.bin: $(B)/boot/boot.bin $(B)/hub/hub_a.img tools/mkimage.py
	@echo "  IMG  $@"
	@python3 tools/mkimage.py factory $(B)/boot/boot.bin $(B)/hub/hub_a.img $@

boot: $(B)/boot/boot.bin
	@$(CROSS)size $(B)/boot/boot.elf

tile: $(B)/tile/tile_a.img $(B)/tile/tile_b.img $(B)/tile/tile_factory.bin
	@$(CROSS)size $(B)/tile/tile_a.elf

hub: $(B)/hub/hub_a.img $(B)/hub/hub_b.img $(B)/hub/hub_factory.bin
	@$(CROSS)size $(B)/hub/hub_a.elf

# nothing that was built is thrown away for being a step on the way
.SECONDARY:

-include $(BOOT_OBJ:.o=.d) $(TILE_OBJ:.o=.d) $(HUB_OBJ:.o=.d)
