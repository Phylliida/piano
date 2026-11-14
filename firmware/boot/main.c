/* The bootloader: the first 8 KB of the chip's flash, the same in a tile and
 * in the hub. The chip starts here after every reset. It looks at the two
 * program slots (core/fw.h), starts the program that fw_boot_choose() names,
 * and that is all: it has no pins, no clock but the one the chip starts
 * with (8 MHz), and it never changes a program. New programs are received
 * by the program that is running, into the other slot.
 *
 * It is not updated over the network. What it is made of is this file,
 * core/fw.c and the chip's flash driver; keep it that small.
 *
 * Nothing here has run on a chip yet.
 *
 * ---- going to the ROM bootloader ----
 *
 * Two ways lead from here into the chip's own ROM bootloader (UART on
 * USART1/2/3, USB DFU): a program asked for it before the reset
 * (port_bootloader(), the word in PORT_MAILBOX), or neither slot holds a
 * program.
 *
 * The chip has no register that maps the boot memory in place of flash
 * (SCFG's MEM_MAP_SEL is read-only), so the way in is "method 2" of
 * Artery's note AN0008, "Jump to Boot Memory": leave a mark that survives a
 * reset, reset, and before anything is switched on (clocks at their reset
 * state, PLL off, no interrupt enabled) load the stack pointer and the start
 * address from the first two words of the boot memory.
 *
 * Three differences from the note: the mark is a word in RAM that no
 * start-up code clears, where the note uses a battery-domain register; the
 * jump is at the top of main(), where the note's is at the top of
 * SystemInit() (Artery's SystemInit only puts the clock back on the
 * internal oscillator); and the vector table register is pointed at the boot
 * memory, which the note leaves alone.
 *
 * Not verified: the note was written for and tried on another chip of the
 * family and names no address for this one. 0x1FFFA400 is the boot memory's
 * start in the AT32F405 datasheet (table 5, 20 KB). It has not been tried.
 * If it does not work, the BOOT0 pad still does. */
#include "flash.h"
#include "fw.h"
#include "port.h"

#define BOOT_ROM  0x1FFFA400u

/* Start what begins with the vector table at `table`: the stack pointer
 * from its first word, the start address from its second. */
static void start(uint32_t table) __attribute__((noreturn));
static void start(uint32_t table)
{
    const uint32_t *v = (const uint32_t *)table;
    uint32_t sp = v[0], pc = v[1];

    SCB->VTOR = table;
    __asm volatile("msr msp, %0\n"
                   "bx %1\n" : : "r"(sp), "r"(pc));
    for (;;) {
    }
}

/* About 4 s: the chip's slow oscillator (LICK, 40 kHz, give or take a
 * third) divided by 64, counted down from 2500. Once started, only a reset
 * stops it; the program has to keep it from running out (port_alive()). The
 * order of the calls is that of Artery's example wdt/wdt_reset. */
static void watchdog_start(void)
{
    wdt_register_write_enable(TRUE);
    wdt_divider_set(WDT_CLK_DIV_64);
    wdt_reload_value_set(2500);
    wdt_counter_reload();
    wdt_enable();
}

int main(void)
{
    /* It only ever writes one word: "this image has been started once". */
    const fw_flash_t flash = { (const uint8_t *)FW_FLASH_BASE, NULL, NULL, flash_fw_program };
    bool trial;
    uint8_t slot;

    if (PORT_MAILBOX == PORT_MAILBOX_ROM) {
        PORT_MAILBOX = 0;
        start(BOOT_ROM);
    }

    slot = fw_boot_choose(&flash, &trial);
    if (slot == FW_NO_SLOT)
        start(BOOT_ROM);

    /* A new program's first run: if it hangs, the watchdog restarts the
     * chip, and the next choice is the program that ran before. */
    if (trial)
        watchdog_start();
    start(fw_slot_addr(slot));
}

/* The C library's start-up calls this before main(); the file that
 * usually holds it (crti.o) is left out of the link. */
void _init(void);
void _init(void)
{
}
