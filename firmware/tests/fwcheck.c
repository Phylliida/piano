/* fw_image_check() behind a pipe.
 *
 * For tests/test_mkimage.py: tools/mkimage.py has the checks of core/fw.c
 * a second time, in Python, and the two have to agree on every file.
 *
 * One line in, one line out:
 *   <0|1> <hex>   the bytes that slot A or B holds, from its first on; the
 *                 rest of the chip is erased
 * The answer: ok <kind> <version> <length> <crc>, all hex, or: bad
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fw.h"

static uint8_t mem[FW_SLOTS_END - FW_FLASH_BASE];
static char line[2 * FW_SLOT_LEN + 16];

static int digit(char c)
{
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1;
}

int main(void)
{
    setvbuf(stdout, NULL, _IOLBF, 0);
    while (fgets(line, sizeof line, stdin)) {
        uint8_t slot = line[0] == '1' ? FW_SLOT_B : FW_SLOT_A;
        uint8_t *p = mem + (fw_slot_addr(slot) - FW_FLASH_BASE);
        const char *text = line + 2;
        fw_image_t image;
        size_t n = 0;

        if ((line[0] != '0' && line[0] != '1') || line[1] != ' ') {
            fprintf(stderr, "fwcheck: a slot, a space, hex digits\n");
            return 2;
        }
        memset(mem, 0xFF, sizeof mem);
        while (digit(text[0]) >= 0 && digit(text[1]) >= 0) {
            if (n == FW_SLOT_LEN) {
                fprintf(stderr, "fwcheck: more than a slot holds\n");
                return 2;
            }
            p[n++] = (uint8_t)(digit(text[0]) << 4 | digit(text[1]));
            text += 2;
        }
        if (fw_image_check(mem, slot, &image))
            printf("ok %x %x %x %x\n", (unsigned)image.kind, (unsigned)image.version,
                   (unsigned)image.length, (unsigned)image.crc);
        else
            puts("bad");
    }
    return 0;
}
