#include "fw.h"

#include <string.h>

#include "proto.h"
#include "util.h"

#define ERASED  0xFFFFFFFFu

static const uint8_t *slot_mem(const uint8_t *mem, uint8_t slot)
{
    return mem + (fw_slot_addr(slot) - FW_FLASH_BASE);
}

static uint32_t trailer_addr(uint8_t slot)
{
    return fw_slot_addr(slot) + FW_SLOT_LEN - FW_TRAILER_LEN;
}

static const uint8_t *trailer(const uint8_t *mem, uint8_t slot)
{
    return mem + (trailer_addr(slot) - FW_FLASH_BASE);
}

/* ------------------------------------------------------------ what is there --- */

bool fw_image_check(const uint8_t *mem, uint8_t slot, fw_image_t *out)
{
    const uint8_t *p = slot_mem(mem, slot), *h = p + FW_HEADER_AT;
    uint32_t base = fw_slot_addr(slot);
    uint32_t length = get32(h + FW_H_LENGTH);
    uint32_t stack = get32(p), start = get32(p + 4);

    if (get32(h + FW_H_MAGIC) != FW_IMAGE_MAGIC || get32(h + FW_H_BASE) != base)
        return false;
    if (length < FW_IMAGE_MIN || length > FW_IMAGE_MAX || length % 4u)
        return false;
    /* what the chip loads first: the stack pointer, and where to start */
    if (stack <= FW_RAM_BASE || stack > FW_RAM_END || stack % 4u)
        return false;
    if (!(start & 1u) || start - 1u < base + 8u || start - 1u >= base + length - 4u)
        return false;
    uint32_t crc = get32(p + length - 4u);
    if (crc != crc32_ieee(p, length - 4u))
        return false;
    if (out) {
        out->kind = h[FW_H_KIND];
        out->version = get16(h + FW_H_VERSION);
        out->length = length;
        out->crc = crc;
    }
    return true;
}

/* A word that is not erased counts as written, whatever it reads: the power
 * may have gone while it was being written, and it cannot be written again. */
uint8_t fw_slot_state(const uint8_t *mem, uint8_t slot)
{
    const uint8_t *t = trailer(mem, slot);

    if (get32(t + FW_T_MAGIC) != FW_TRAILER_MAGIC) {
        /* begin wrote its number, commit did not get to the magic, or not
         * to the end of it */
        for (uint32_t i = 0; i < FW_TRAILER_LEN; i += 4) {
            if (get32(t + i) != ERASED)
                return FW_PARTIAL;
        }
        return FW_NONE;
    }
    if (get32(t + FW_T_OK) != ERASED)
        return FW_GOOD;
    if (get32(t + FW_T_TRIED) != ERASED)
        return FW_TRIED;
    return FW_NEW;
}

uint32_t fw_slot_seq(const uint8_t *mem, uint8_t slot)
{
    const uint8_t *t = trailer(mem, slot);

    return get32(t + FW_T_MAGIC) == FW_TRAILER_MAGIC ? get32(t + FW_T_SEQ) : 0;
}

static bool mark(const fw_flash_t *f, uint8_t slot, uint32_t field)
{
    static const uint8_t zero[4] = { 0, 0, 0, 0 };

    if (!f->program || !f->program(f->ctx, trailer_addr(slot) + field, zero, 4))
        return false;
    return get32(trailer(f->mem, slot) + field) != ERASED;
}

/* ---------------------------------------------------------- the bootloader --- */

/* The whole image in `state` to start first: the newer one, slot A if that
 * does not tell them apart. `skip` is a slot not to offer again. */
static uint8_t best(const fw_flash_t *f, uint8_t state, const bool whole[FW_SLOTS], uint8_t skip)
{
    uint8_t found = FW_NO_SLOT;

    for (uint8_t s = 0; s < FW_SLOTS; s++) {
        if (s == skip || !whole[s] || fw_slot_state(f->mem, s) != state)
            continue;
        if (found == FW_NO_SLOT || fw_slot_seq(f->mem, s) > fw_slot_seq(f->mem, found))
            found = s;
    }
    return found;
}

uint8_t fw_boot_choose(const fw_flash_t *f, bool *trial)
{
    /* GOOD before NONE: an image without a trailer has no age, and the one
     * that was confirmed came after it. TRIED last: it was started once and
     * nobody confirmed it, but it is better than no program. PARTIAL never:
     * nobody checked what it is. */
    static const uint8_t order[] = { FW_GOOD, FW_NONE, FW_TRIED };
    bool whole[FW_SLOTS];
    uint8_t s, skip = FW_NO_SLOT;

    *trial = false;
    for (s = 0; s < FW_SLOTS; s++)
        whole[s] = fw_image_check(f->mem, s, NULL);

    /* A new image gets one start. If that cannot be written down, it gets
     * none: it would be started again and again. */
    while ((s = best(f, FW_NEW, whole, skip)) != FW_NO_SLOT) {
        if (mark(f, s, FW_T_TRIED)) {
            *trial = true;
            return s;
        }
        skip = s;
    }
    for (size_t i = 0; i < sizeof order; i++) {
        s = best(f, order[i], whole, skip);
        if (s != FW_NO_SLOT)
            return s;
    }
    return FW_NO_SLOT;
}

/* ----------------------------------------------------- the running program --- */

void fw_init(fw_t *fw, const fw_flash_t *flash, uint8_t kind, uint8_t slot)
{
    memset(fw, 0, sizeof *fw);
    fw->kind = kind;
    fw->slot = FW_NO_SLOT;
    if (!flash || !flash->mem || slot >= FW_SLOTS)
        return;
    fw->flash = *flash;
    fw->slot = slot;
    fw->trial = fw_slot_state(flash->mem, slot) == FW_TRIED;
}

static uint8_t other(const fw_t *fw)
{
    return (uint8_t)(1u - fw->slot);
}

static bool erase(fw_t *fw, uint32_t addr)
{
    return fw->flash.erase && fw->flash.erase(fw->flash.ctx, addr);
}

static bool program(fw_t *fw, uint32_t addr, const uint8_t *data, uint32_t len)
{
    if (!fw->flash.program || !fw->flash.program(fw->flash.ctx, addr, data, len))
        return false;
    return memcmp(fw->flash.mem + (addr - FW_FLASH_BASE), data, len) == 0;
}

uint8_t fw_begin(fw_t *fw, uint32_t length)
{
    uint8_t seq[4], to;
    uint32_t base;

    if (fw->slot == FW_NO_SLOT)
        return ST_UNKNOWN;
    if (fw->trial)
        return ST_BUSY;
    if (length < FW_IMAGE_MIN || length > FW_IMAGE_MAX || length % 4u)
        return ST_BAD_ARG;

    /* The first sector first: without its header the old image is no image
     * any more, whatever its trailer says. Then the trailer, and into it
     * the new image's number: from here to commit the slot is PARTIAL, and
     * the bootloader does not take what is in it for a program put there
     * from outside. The other sectors are erased as the writes reach them. */
    to = other(fw);
    base = fw_slot_addr(to);
    fw->open = false;
    fw->staged = false;
    put32(seq, fw_slot_seq(fw->flash.mem, fw->slot) + 1u);
    if (!erase(fw, base) || !erase(fw, base + FW_SLOT_LEN - FW_SECTOR)
        || !program(fw, trailer_addr(to) + FW_T_SEQ, seq, 4))
        return ST_FAILED;
    fw->open = true;
    fw->length = length;
    fw->next = 0;
    fw->erased = FW_SECTOR;
    return ST_OK;
}

uint8_t fw_write(fw_t *fw, uint32_t offset, const uint8_t *data, uint8_t len, uint32_t *next)
{
    uint32_t base;

    *next = fw->open ? fw->next : 0;
    if (fw->slot == FW_NO_SLOT)
        return ST_UNKNOWN;
    if (!fw->open)
        return ST_FAILED;
    if (offset != fw->next || len == 0 || len % 4u || len > fw->length - offset)
        return ST_BAD_ARG;

    base = fw_slot_addr(other(fw));
    while (offset + len > fw->erased) {
        if (!erase(fw, base + fw->erased))
            return ST_FAILED;           /* the same write may be tried again */
        fw->erased += FW_SECTOR;
    }
    if (!program(fw, base + offset, data, len)) {
        /* some of it may be in the flash now, which takes no second write */
        fw->open = false;
        *next = 0;
        return ST_FAILED;
    }
    fw->next += len;
    *next = fw->next;
    return ST_OK;
}

uint8_t fw_commit(fw_t *fw, uint32_t *next)
{
    uint8_t magic[4], to;
    fw_image_t image;

    *next = fw->open ? fw->next : 0;
    if (fw->slot == FW_NO_SLOT)
        return ST_UNKNOWN;
    if (!fw->open)
        return fw->staged ? ST_OK : ST_FAILED;
    if (fw->next != fw->length)
        return ST_BAD_ARG;

    to = other(fw);
    fw->open = false;
    *next = 0;
    if (!fw_image_check(fw->flash.mem, to, &image) || image.length != fw->length
        || image.kind != fw->kind)
        return ST_FAILED;

    /* The magic is the last word of an update: with it the image is NEW,
     * and a magic that the power cut short leaves it PARTIAL. */
    put32(magic, FW_TRAILER_MAGIC);
    if (!program(fw, trailer_addr(to) + FW_T_MAGIC, magic, 4))
        return ST_FAILED;
    fw->staged = true;
    return ST_OK;
}

uint8_t fw_confirm(fw_t *fw)
{
    if (fw->slot == FW_NO_SLOT)
        return ST_UNKNOWN;
    if (fw_slot_state(fw->flash.mem, fw->slot) != FW_TRIED)
        return ST_OK;                   /* nothing to confirm */
    if (!mark(&fw->flash, fw->slot, FW_T_OK))
        return ST_FAILED;
    fw->trial = false;
    return ST_OK;
}

void fw_info(const fw_t *fw, uint8_t out[FW_INFO_LEN])
{
    fw_image_t image;
    uint8_t flags = 0;

    memset(out, 0, FW_INFO_LEN);
    out[0] = fw->slot;
    if (fw->slot == FW_NO_SLOT)
        return;
    out[1] = fw_slot_state(fw->flash.mem, fw->slot);
    out[2] = fw_slot_state(fw->flash.mem, other(fw));
    if (fw_image_check(fw->flash.mem, fw->slot, &image)) {
        put16(out + 4, image.version);
        put32(out + 6, image.crc);
    }
    if (fw_image_check(fw->flash.mem, other(fw), &image)) {
        flags |= FW_F_OTHER_VALID;
        put32(out + 10, image.crc);
    }
    if (fw->open) {
        flags |= FW_F_OPEN;
        put32(out + 14, fw->next);
    }
    if (fw->staged)
        flags |= FW_F_STAGED;
    out[3] = flags;
}
