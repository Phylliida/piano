/* The bootloader (boot/): the first 8 KB of the chip's flash. */

MEMORY
{
    FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 8K
    RAM   (xrw) : ORIGIN = 0x20000010, LENGTH = 102K - 16
}

INCLUDE sections.ld
