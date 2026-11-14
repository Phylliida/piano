/* Firmware update: how a program gets into the chip through the tile
 * network, and how the chip decides at each start which program to run.
 * DESIGN.md, "Firmware update", is this file in words.
 *
 * The chip's 256 KB of flash, for a tile and for the hub alike:
 *
 *   0x08000000   8 KB    the bootloader (boot/): chooses a slot and jumps
 *   0x08002000   122 KB  slot A
 *   0x08020800   122 KB  slot B
 *   0x0803F000   2 KB    free
 *   0x0803F800   2 KB    the settings record (store.h)
 *
 * A slot holds one program image and, in its last 2 KB sector, a trailer of
 * four words. The program is linked twice, once for each slot's address. The
 * running program writes a new image into the slot it does not run from;
 * nothing is ever copied.
 *
 * An image is the program's binary. 0x200 bytes in, behind the vector
 * table, is its header: magic, kind (tile or hub), version, the address it
 * was linked for, its length. Its last four bytes are the CRC-32 of
 * everything before them.
 *
 * The trailer says what became of the image: magic, seq, tried, ok. Flash
 * words go from erased to written once, so the image's life is
 *   NONE     no trailer at all: the image was put there from outside (ROM, SWD)
 *   PARTIAL  seq and no magic: it is being received, or that was cut short.
 *            Never started, whatever is in the slot.
 *   NEW      received and checked, never started
 *   TRIED    the bootloader started it once
 *   GOOD     it ran and was told it did well (confirm)
 * and seq orders the images by age.
 *
 * Three things use this file: the bootloader (fw_boot_choose), the tile and
 * the hub program (fw_t: receive an image, confirm the running one), and
 * the simulator, which plays the chip. The flash itself comes from the
 * caller, as memory to read and two functions to change it. */
#ifndef PIANO_FW_H
#define PIANO_FW_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* ---- the chip ---- */
#define FW_FLASH_BASE     0x08000000u
#define FW_SECTOR         2048u
#define FW_BOOT_LEN       0x2000u
#define FW_SLOT_LEN       0x1E800u
#define FW_SLOT_A_ADDR    (FW_FLASH_BASE + FW_BOOT_LEN)
#define FW_SLOT_B_ADDR    (FW_SLOT_A_ADDR + FW_SLOT_LEN)
#define FW_SLOTS_END      (FW_SLOT_B_ADDR + FW_SLOT_LEN)    /* 0x0803F000 */
#define FW_RAM_BASE       0x20000000u
#define FW_RAM_END        0x20019800u   /* 102 KB */

enum { FW_SLOT_A = 0, FW_SLOT_B = 1, FW_SLOTS = 2 };
#define FW_NO_SLOT        0xFFu

/* ---- an image ---- */
#define FW_HEADER_AT      0x200u
#define FW_HEADER_LEN     16u
#define FW_IMAGE_MAGIC    0x474D4950u   /* "PIMG" */
#define FW_IMAGE_MIN      (FW_HEADER_AT + FW_HEADER_LEN + 4u)
#define FW_IMAGE_MAX      (FW_SLOT_LEN - FW_SECTOR)         /* the last sector is the trailer's */

/* The header's fields, as offsets from FW_HEADER_AT. Little-endian. */
#define FW_H_MAGIC        0     /* 4 */
#define FW_H_KIND         4     /* 1: KIND_TILE or KIND_HUB */
#define FW_H_VERSION      6     /* 2: FW_VERSION */
#define FW_H_BASE         8     /* 4: the slot's address */
#define FW_H_LENGTH       12    /* 4: the whole image, its CRC included; a multiple of 4 */

/* ---- a slot's trailer: its last 16 bytes ---- */
#define FW_TRAILER_LEN    16u
#define FW_TRAILER_MAGIC  0x4C525450u   /* "PTRL" */
#define FW_T_MAGIC        0     /* written last, by commit */
#define FW_T_SEQ          4     /* written first, by begin */
#define FW_T_TRIED        8     /* erased: not yet */
#define FW_T_OK           12    /* erased: not yet */

enum { FW_NONE = 0, FW_NEW = 1, FW_TRIED = 2, FW_GOOD = 3, FW_PARTIAL = 4 };

/* ---- times ---- */
#define FW_TRIAL_MS       60000u    /* a program on trial that hears no confirm restarts after this */
#define FW_GUARD_MS       1000u     /* ... and takes no REBOOT this soon after its start: that
                                       is the one that started it, said again */

/* The chip's flash. `mem` is all of it as memory, from FW_FLASH_BASE.
 * erase() erases the 2 KB sector at `addr`; program() writes whole words
 * onto erased flash. Both take chip addresses, stand still until the flash
 * is done, and say whether it went well. */
typedef struct {
    const uint8_t *mem;
    void *ctx;
    bool (*erase)(void *ctx, uint32_t addr);
    bool (*program)(void *ctx, uint32_t addr, const uint8_t *data, uint32_t len);
} fw_flash_t;

static inline uint32_t fw_slot_addr(uint8_t slot)
{
    return slot == FW_SLOT_A ? FW_SLOT_A_ADDR : FW_SLOT_B_ADDR;
}

/* The slot a program linked for `base` runs from, or FW_NO_SLOT. */
static inline uint8_t fw_slot_of(uint32_t base)
{
    return base == FW_SLOT_A_ADDR ? FW_SLOT_A : base == FW_SLOT_B_ADDR ? FW_SLOT_B : FW_NO_SLOT;
}

/* ---- looking at a slot ---- */

typedef struct {
    uint8_t kind;
    uint16_t version;
    uint32_t length;
    uint32_t crc;
} fw_image_t;

/* Is there a whole image in the slot: header, length, checksum, a stack in
 * RAM and a start address inside the image? `out` may be NULL. */
bool fw_image_check(const uint8_t *mem, uint8_t slot, fw_image_t *out);

uint8_t fw_slot_state(const uint8_t *mem, uint8_t slot);    /* FW_NONE .. FW_PARTIAL */
uint32_t fw_slot_seq(const uint8_t *mem, uint8_t slot);     /* 0 for FW_NONE and FW_PARTIAL */

/* ---- the bootloader ----
 * The slot to start, or FW_NO_SLOT when neither holds a program. A NEW
 * image is marked TRIED here, before its first start; *trial then says that
 * the watchdog must run, so that a program which hangs is not started a
 * second time. */
uint8_t fw_boot_choose(const fw_flash_t *f, bool *trial);

/* ---- the running program ---- */

#define FW_CHUNK          48u   /* the most one write carries */
#define FW_INFO_LEN       18u

typedef struct {
    fw_flash_t flash;
    uint8_t kind;           /* what this program is */
    uint8_t slot;           /* where it runs from; FW_NO_SLOT: nowhere it knows, no updates */
    bool trial;             /* it is TRIED and not yet GOOD */

    bool open;              /* between begin and commit */
    bool staged;            /* an image was committed since this program started */
    uint32_t length;        /* of the image being received */
    uint32_t next;          /* the offset the next write must have */
    uint32_t erased;        /* the other slot is erased from 0 up to here */
} fw_t;

/* flash may be NULL and slot FW_NO_SLOT: then every call below answers
 * ST_UNKNOWN. */
void fw_init(fw_t *fw, const fw_flash_t *flash, uint8_t kind, uint8_t slot);

/* These return a REPLY status (proto.h).
 *
 * begin: the other slot is going to get an image of `length` bytes. What
 *   it held is gone from here on, and the slot is PARTIAL until commit.
 *   ST_BUSY while this program is on trial: the other slot is then the
 *   only program known to work.
 * write: the image's bytes, in order: `offset` must be `*next`. In *next,
 *   whatever the status, the offset the next write must have.
 * commit: the image is complete. It is checked (checksum, kind, slot) and
 *   marked NEW: the bootloader starts it at the next restart.
 * confirm: the running program did well; it becomes GOOD. */
uint8_t fw_begin(fw_t *fw, uint32_t length);
uint8_t fw_write(fw_t *fw, uint32_t offset, const uint8_t *data, uint8_t len, uint32_t *next);
uint8_t fw_commit(fw_t *fw, uint32_t *next);
uint8_t fw_confirm(fw_t *fw);

/* What FW_INFO answers:
 *   0   the running slot (0: A, 1: B, 0xFF: none)
 *   1   its state (FW_NONE .. FW_PARTIAL)
 *   2   the other slot's state
 *   3   flags (FW_F_)
 *   4   the running image's version (2)
 *   6   its CRC (4)
 *   10  the other slot's image's CRC (4); 0 without a whole image
 *   14  while receiving: the offset the next write must have (4) */
#define FW_F_OTHER_VALID  0x01u     /* the other slot holds a whole image */
#define FW_F_OPEN         0x02u     /* receiving: between begin and commit */
#define FW_F_STAGED       0x04u     /* an image was committed since this program started */
void fw_info(const fw_t *fw, uint8_t out[FW_INFO_LEN]);

#endif
