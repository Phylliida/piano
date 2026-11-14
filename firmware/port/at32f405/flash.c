/* The saved record in the last flash sector. See flash.h. */
#include "flash.h"

#include <string.h>

bool flash_save(const uint8_t *data, size_t len)
{
    bool ok = true;

    if (len > FLASH_RECORD_SIZE)
        return false;

    /* The erase is timed by the chip's 8 MHz oscillator (HICK), which is
     * on since reset and which nothing here switches off. */
    flash_unlock();
    flash_flag_clear(FLASH_ODF_FLAG | FLASH_PRGMERR_FLAG | FLASH_EPPERR_FLAG);
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
