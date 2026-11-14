#include "flashsim.h"

#include <string.h>

#include "util.h"

static uint8_t noise(flashsim_t *f)
{
    f->noise ^= f->noise << 13;
    f->noise ^= f->noise >> 17;
    f->noise ^= f->noise << 5;
    return (uint8_t)(f->noise >> 8);
}

void flashsim_init(flashsim_t *f, uint32_t seed)
{
    memset(f, 0, sizeof *f);
    memset(f->mem, 0xFF, sizeof f->mem);
    f->noise = seed ? seed : 1;
}

/* Does the n-th call from now fail? Counts down. */
static bool fails(flashsim_t *f)
{
    if (f->fail_at == 0)
        return false;
    return --f->fail_at == 0;
}

static bool in_slots(uint32_t addr, uint32_t len)
{
    return addr >= FW_SLOT_A_ADDR && addr <= FW_SLOTS_END && len <= FW_SLOTS_END - addr;
}

/* A sector that was not erased to the end: some of it still holds what it
 * held, some is erased, some is neither. */
static void half_erase(flashsim_t *f, uint32_t addr)
{
    uint8_t *p = f->mem + (addr - FW_FLASH_BASE);

    for (uint32_t i = 0; i < FW_SECTOR; i++) {
        uint8_t r = noise(f);

        if (r < 100)
            p[i] = 0xFF;
        else if (r < 150)
            p[i] |= noise(f);
    }
}

/* A write that stopped half way: the first half is there, then one word
 * with only some of its bits, then nothing. */
static void half_program(flashsim_t *f, uint32_t addr, const uint8_t *data, uint32_t len)
{
    uint8_t *p = f->mem + (addr - FW_FLASH_BASE);
    uint32_t done = (len / 8u) * 4u;

    memcpy(p, data, done);
    for (uint32_t i = done; i < done + 4u; i++)
        p[i] = (uint8_t)(data[i] | noise(f));
}

static bool sim_erase(void *ctx, uint32_t addr)
{
    flashsim_t *f = ctx;

    if (addr % FW_SECTOR || !in_slots(addr, FW_SECTOR)) {
        f->stray++;
        return false;
    }
    f->erases++;
    f->last_erase = true;
    f->last_addr = addr;
    f->last_len = FW_SECTOR;
    if (fails(f)) {
        half_erase(f, addr);
        return false;
    }
    memset(f->mem + (addr - FW_FLASH_BASE), 0xFF, FW_SECTOR);
    return true;
}

static bool sim_program(void *ctx, uint32_t addr, const uint8_t *data, uint32_t len)
{
    flashsim_t *f = ctx;
    uint8_t *p = f->mem + (addr - FW_FLASH_BASE);

    if (addr % 4u || len % 4u || len == 0 || !in_slots(addr, len)) {
        f->stray++;
        return false;
    }
    for (uint32_t i = 0; i < len; i++) {
        if (p[i] != 0xFF) {
            f->rewrites++;
            return false;
        }
    }
    f->programs++;
    f->last_erase = false;
    f->last_addr = addr;
    f->last_len = len;
    if (fails(f)) {
        half_program(f, addr, data, len);
        return false;
    }
    memcpy(p, data, len);
    return true;
}

fw_flash_t flashsim_flash(flashsim_t *f)
{
    fw_flash_t flash = { f->mem, f, sim_erase, sim_program };

    return flash;
}

void flashsim_cut(flashsim_t *f)
{
    uint8_t was[FW_CHUNK];

    if (f->last_len == 0)
        return;
    if (f->last_erase) {
        half_erase(f, f->last_addr);
    } else if (f->last_len <= sizeof was) {
        uint8_t *p = f->mem + (f->last_addr - FW_FLASH_BASE);

        memcpy(was, p, f->last_len);
        memset(p, 0xFF, f->last_len);
        half_program(f, f->last_addr, was, f->last_len);
    }
    f->last_len = 0;
}

/* ------------------------------------------------------------------ images --- */

void flashsim_image(uint8_t *out, uint32_t length, uint8_t kind, uint8_t slot, uint16_t version,
                    uint8_t act, uint32_t seed)
{
    uint8_t *h = out + FW_HEADER_AT;
    rng_t r;

    rng_seed(&r, seed);
    for (uint32_t i = 0; i < length; i++)
        out[i] = (uint8_t)rng_next(&r);
    put32(out, FW_RAM_END);                                         /* the stack */
    put32(out + 4, (fw_slot_addr(slot) + FLASHSIM_ACT_AT + 8u) | 1u);  /* the start, in Thumb */
    put32(h + FW_H_MAGIC, FW_IMAGE_MAGIC);
    h[FW_H_KIND] = kind;
    h[FW_H_KIND + 1] = 0;
    put16(h + FW_H_VERSION, version);
    put32(h + FW_H_BASE, fw_slot_addr(slot));
    put32(h + FW_H_LENGTH, length);
    memcpy(out + FLASHSIM_ACT_AT, "SIM!", 4);
    out[FLASHSIM_ACT_AT + 4] = act;
    put32(out + length - 4u, crc32_ieee(out, length - 4u));
}

uint8_t flashsim_act(const flashsim_t *f, uint8_t slot)
{
    const uint8_t *p = f->mem + (fw_slot_addr(slot) - FW_FLASH_BASE) + FLASHSIM_ACT_AT;

    return memcmp(p, "SIM!", 4) == 0 ? p[4] : SIM_ACT_RUN;
}

void flashsim_put(flashsim_t *f, uint8_t slot, const uint8_t *image, uint32_t length)
{
    uint8_t *p = f->mem + (fw_slot_addr(slot) - FW_FLASH_BASE);
    uint32_t covered = (length + FW_SECTOR - 1u) / FW_SECTOR * FW_SECTOR;

    memset(p, 0xFF, covered);
    memcpy(p, image, length);
}

void flashsim_trailer(flashsim_t *f, uint8_t slot, uint8_t state, uint32_t seq)
{
    uint8_t *t = f->mem + (fw_slot_addr(slot) - FW_FLASH_BASE) + FW_SLOT_LEN - FW_TRAILER_LEN;

    memset(t, 0xFF, FW_TRAILER_LEN);
    if (state == FW_NONE)
        return;
    put32(t + FW_T_SEQ, seq);
    if (state == FW_PARTIAL)
        return;
    put32(t + FW_T_MAGIC, FW_TRAILER_MAGIC);
    if (state == FW_TRIED || state == FW_GOOD)
        put32(t + FW_T_TRIED, 0);
    if (state == FW_GOOD)
        put32(t + FW_T_OK, 0);
}
