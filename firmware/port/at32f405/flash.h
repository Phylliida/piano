/* Writing the chip's own flash: the saved record in the last 2 KB sector
 * (0x0803F800), and the two program slots (core/fw.h).
 *
 * While flash is erased or written the chip stands still: no instruction
 * runs, no interrupt. Only DMA and the timers go on. A sector's erase takes
 * about 18 ms, a word about 50 us. */
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
 * Takes about 20 ms. If power goes while this runs, the sector holds no
 * record. */
bool flash_save(const uint8_t *data, size_t len);

/* The two functions of an fw_flash_t (core/fw.h), whose `mem` is
 * FW_FLASH_BASE. Both keep to the two slots, and erase never touches the
 * slot this program runs from. `ctx` is not used. */
bool flash_fw_erase(void *ctx, uint32_t addr);
bool flash_fw_program(void *ctx, uint32_t addr, const uint8_t *data, uint32_t len);

#endif
