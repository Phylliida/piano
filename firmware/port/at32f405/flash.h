/* The saved record: the last 2 KB sector of the chip's flash (0x0803F800),
 * which the linker script keeps out of the program. */
#ifndef PIANO_FLASH_H
#define PIANO_FLASH_H

#include "port.h"

#define FLASH_RECORD_ADDR  0x0803F800u
#define FLASH_RECORD_SIZE  2048u

/* What the sector holds now; give it to tile_init() or hub_init(), which
 * know a record from rubbish. */
static inline const uint8_t *flash_record(void)
{
    return (const uint8_t *)FLASH_RECORD_ADDR;
}

/* Erase the sector and write `len` bytes (at most FLASH_RECORD_SIZE) to its
 * start. True if what is there afterwards is what was asked for.
 *
 * Takes about 18 ms, and for most of it the chip stands still: no
 * interrupt runs while flash is being erased. Only DMA goes on. If power
 * goes while this runs, the sector holds no record. */
bool flash_save(const uint8_t *data, size_t len);

#endif
