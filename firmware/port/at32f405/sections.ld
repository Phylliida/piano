/* What goes where inside one program for the AT32F405RCT7. Not given to the
 * linker by itself: boot.ld, slot_a.ld and slot_b.ld say which part of the
 * chip's flash the program is for (FLASH) and include this file. The map of
 * the whole flash is in core/fw.h.
 *
 * RAM is the same for all three: the chip's 102 KB at 0x20000000 without
 * its first 16 bytes. The first word is where a program leaves a message
 * for the bootloader across a reset (port.h, PORT_MAILBOX), so no program
 * may keep a variable there.
 *
 * The symbols are the ones Artery's startup file (startup_at32f402_405.s)
 * uses. */

ENTRY(Reset_Handler)

_estack = ORIGIN(RAM) + LENGTH(RAM);    /* the stack starts at the top of RAM */
_min_stack = 0x1000;                    /* the link fails if less than this is left for it */

_image_start = ORIGIN(FLASH);           /* where this program begins: its vector table */

SECTIONS
{
    /* The vector table, filled up to 0x200 bytes: that is where the
     * bootloader and the tools look for an image's header (FW_HEADER_AT). */
    .isr_vector :
    {
        . = ALIGN(4);
        KEEP(*(.isr_vector))
        . = ALIGN(0x200);
    } >FLASH
    ASSERT(SIZEOF(.isr_vector) == 0x200, "the vector table does not end at 0x200")

    /* The image's header (port.h, PORT_IMAGE_HEADER). The bootloader has none. */
    .fw_header :
    {
        KEEP(*(.fw_header))
    } >FLASH

    .text :
    {
        . = ALIGN(4);
        *(.text)
        *(.text*)
        *(.glue_7)
        *(.glue_7t)
        *(.eh_frame)
        KEEP(*(.init))
        KEEP(*(.fini))
        . = ALIGN(4);
        _etext = .;
    } >FLASH

    .rodata :
    {
        . = ALIGN(4);
        *(.rodata)
        *(.rodata*)
        . = ALIGN(4);
    } >FLASH

    .ARM.extab : { *(.ARM.extab* .gnu.linkonce.armextab.*) } >FLASH
    .ARM :
    {
        __exidx_start = .;
        *(.ARM.exidx*)
        __exidx_end = .;
    } >FLASH

    .preinit_array :
    {
        PROVIDE_HIDDEN(__preinit_array_start = .);
        KEEP(*(.preinit_array*))
        PROVIDE_HIDDEN(__preinit_array_end = .);
    } >FLASH
    .init_array :
    {
        PROVIDE_HIDDEN(__init_array_start = .);
        KEEP(*(SORT(.init_array.*)))
        KEEP(*(.init_array*))
        PROVIDE_HIDDEN(__init_array_end = .);
    } >FLASH
    .fini_array :
    {
        PROVIDE_HIDDEN(__fini_array_start = .);
        KEEP(*(SORT(.fini_array.*)))
        KEEP(*(.fini_array*))
        PROVIDE_HIDDEN(__fini_array_end = .);
    } >FLASH

    /* Variables with a starting value: kept in flash, copied to RAM by the
     * startup code. */
    _sidata = LOADADDR(.data);
    .data :
    {
        . = ALIGN(4);
        _sdata = .;
        *(.data)
        *(.data*)
        . = ALIGN(4);
        _edata = .;
    } >RAM AT>FLASH

    /* Variables that start as zero: cleared by the startup code. */
    .bss (NOLOAD) :
    {
        . = ALIGN(4);
        _sbss = .;
        __bss_start__ = _sbss;
        *(.bss)
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        _ebss = .;
        __bss_end__ = _ebss;
    } >RAM

    /* Nothing is put here; it only makes the link fail when the stack
     * would have less than _min_stack. */
    .stack_check (NOLOAD) :
    {
        . = ALIGN(8);
        PROVIDE(end = .);
        PROVIDE(_end = .);
        . = . + _min_stack;
        . = ALIGN(8);
    } >RAM

    .ARM.attributes 0 : { *(.ARM.attributes) }
}
