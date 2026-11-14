/* The same program, linked for slot B. See slot_a.ld. */

MEMORY
{
    FLASH (rx)  : ORIGIN = 0x08020800, LENGTH = 120K - 4
    RAM   (xrw) : ORIGIN = 0x20000010, LENGTH = 102K - 16
}

INCLUDE sections.ld
