/* A tile's or the hub's program, linked for slot A (core/fw.h). Of the
 * slot's 122 KB the last 2 KB sector is its trailer's, and the image's last
 * four bytes are the checksum that tools/mkimage.py puts behind the program. */

MEMORY
{
    FLASH (rx)  : ORIGIN = 0x08002000, LENGTH = 120K - 4
    RAM   (xrw) : ORIGIN = 0x20000010, LENGTH = 102K - 16
}

INCLUDE sections.ld
