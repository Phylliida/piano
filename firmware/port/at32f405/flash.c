/* Writing the chip's own flash. See flash.h. */
#include "flash.h"

#include <string.h>

/* Erasing and writing are timed by the chip's 8 MHz oscillator (HICK),
 * which is on since reset and which nothing here switches off. */
static void flash_open(void)
{
    flash_unlock();
    flash_flag_clear(FLASH_ODF_FLAG | FLASH_PRGMERR_FLAG | FLASH_EPPERR_FLAG);
}

bool flash_save(const uint8_t *data, size_t len)
{
    bool ok = true;

    if (len > FLASH_RECORD_SIZE)
        return false;

    flash_open();
    if (flash_sector_erase(FLASH_RECORD_ADDR) != FLASH_OPERATE_DONE)
        ok = false;

    /* A word at a time; the last word is filled up with 0xFF, which is
     * what erased flash reads as. */
    for (size_t at = 0; ok && at < len; at += 4) {
        uint8_t word[4] = { 0xFF, 0xFF, 0xFF, 0xFF };
        uint32_t w;

        memcpy(word, data + at, len - at < 4 ? len - at : 4);
        memcpy(&w, word, 4);
        if (flash_word_program(FLASH_RECORD_ADDR + at, w) != FLASH_OPERATE_DONE)
            ok = false;
    }
    flash_lock();

    return ok && memcmp((const void *)FLASH_RECORD_ADDR, data, len) == 0;
}

/* ---- the program slots ---- */

static bool in_slots(uint32_t addr, uint32_t len)
{
    return addr >= FW_SLOT_A_ADDR && addr <= FW_SLOTS_END && len <= FW_SLOTS_END - addr;
}

bool flash_fw_erase(void *ctx, uint32_t addr)
{
    bool ok;

    (void)ctx;
    if (addr % FW_SECTOR || !in_slots(addr, FW_SECTOR))
        return false;
    if (addr - (uint32_t)_image_start < FW_SLOT_LEN)
        return false;                   /* the program that is running */

    flash_open();
    ok = flash_sector_erase(addr) == FLASH_OPERATE_DONE;
    flash_lock();
    return ok;
}

bool flash_fw_program(void *ctx, uint32_t addr, const uint8_t *data, uint32_t len)
{
    bool ok = true;

    (void)ctx;
    if (addr % 4u || len % 4u || len == 0 || !in_slots(addr, len))
        return false;

    flash_open();
    for (uint32_t at = 0; ok && at < len; at += 4) {
        uint32_t w;

        memcpy(&w, data + at, 4);
        ok = flash_word_program(addr + at, w) == FLASH_OPERATE_DONE;
    }
    flash_lock();
    return ok;
}
