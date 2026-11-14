/* core/fw: images and slots, the bootloader's choice, receiving an image,
 * and what a failing flash or a power cut at any moment leaves behind. */
#include "flashsim.h"
#include "fw.h"
#include "proto.h"
#include "t.h"
#include "util.h"

static flashsim_t chip;                 /* 256 KB: not for the stack */
static fw_flash_t flash;
static uint8_t image[FW_IMAGE_MAX], other_image[FW_IMAGE_MAX];

#define TILE_LEN  15120u                /* about what the tile's program is */

static void chip_new(void)
{
    flashsim_init(&chip, 20261113);
    flash = flashsim_flash(&chip);
}

/* A chip as it leaves the bench: a tile's program in slot A, no trailer. */
static void chip_factory(void)
{
    chip_new();
    flashsim_image(image, TILE_LEN, KIND_TILE, FW_SLOT_A, 1, SIM_ACT_RUN, 1);
    flashsim_put(&chip, FW_SLOT_A, image, TILE_LEN);
}

static void recrc(uint8_t *img, uint32_t length)
{
    put32(img + length - 4, crc32_ieee(img, length - 4));
}

static bool whole(uint8_t slot)
{
    return fw_image_check(chip.mem, slot, NULL);
}

static uint8_t boot(bool *trial)
{
    return fw_boot_choose(&flash, trial);
}

/* The flash calls never left the two slots and never wrote a word twice. */
static void check_clean(void)
{
    CHECK_EQ(chip.stray, 0);
    CHECK_EQ(chip.rewrites, 0);
}

/* begin, every write, commit: the first status that is not ST_OK. */
static uint8_t send(fw_t *fw, const uint8_t *img, uint32_t length)
{
    uint32_t next = 0;
    uint8_t st = fw_begin(fw, length);

    for (uint32_t at = 0; st == ST_OK && at < length; at = next) {
        uint32_t n = length - at < FW_CHUNK ? length - at : FW_CHUNK;

        st = fw_write(fw, at, img + at, (uint8_t)n, &next);
        if (st == ST_OK)
            CHECK_EQ(next, at + n);
    }
    if (st == ST_OK)
        st = fw_commit(fw, &next);
    return st;
}

/* ------------------------------------------------------------------ images --- */

static void test_image_check(void)
{
    fw_image_t got;

    chip_new();
    CHECK(!whole(FW_SLOT_A));
    CHECK(!whole(FW_SLOT_B));

    flashsim_image(image, TILE_LEN, KIND_TILE, FW_SLOT_A, 0x0102, SIM_ACT_RUN, 5);
    flashsim_put(&chip, FW_SLOT_A, image, TILE_LEN);
    CHECK(fw_image_check(chip.mem, FW_SLOT_A, &got));
    CHECK_EQ(got.kind, KIND_TILE);
    CHECK_EQ(got.version, 0x0102);
    CHECK_EQ(got.length, TILE_LEN);
    CHECK_EQ(got.crc, get32(image + TILE_LEN - 4));
    CHECK_EQ(got.crc, crc32_ieee(image, TILE_LEN - 4));
    CHECK(!whole(FW_SLOT_B));

    /* the same bytes in the other slot: linked for the wrong address */
    flashsim_put(&chip, FW_SLOT_B, image, TILE_LEN);
    CHECK(!whole(FW_SLOT_B));
    flashsim_image(image, TILE_LEN, KIND_HUB, FW_SLOT_B, 7, SIM_ACT_RUN, 6);
    flashsim_put(&chip, FW_SLOT_B, image, TILE_LEN);
    CHECK(fw_image_check(chip.mem, FW_SLOT_B, &got));
    CHECK_EQ(got.kind, KIND_HUB);

    /* the smallest and the largest there can be */
    flashsim_image(image, FLASHSIM_IMAGE_MIN, KIND_TILE, FW_SLOT_A, 1, SIM_ACT_RUN, 7);
    flashsim_put(&chip, FW_SLOT_A, image, FLASHSIM_IMAGE_MIN);
    CHECK(whole(FW_SLOT_A));
    flashsim_image(image, FW_IMAGE_MAX, KIND_TILE, FW_SLOT_A, 1, SIM_ACT_RUN, 8);
    flashsim_put(&chip, FW_SLOT_A, image, FW_IMAGE_MAX);
    CHECK(whole(FW_SLOT_A));
}

/* One thing wrong at a time, with the checksum made right again where the
 * checksum is not what is being tried. */
static void test_image_faults(void)
{
    static const struct {
        uint32_t at, value;
        bool keep_crc;
    } fault[] = {
        { FW_HEADER_AT + FW_H_MAGIC, 0x474D4951u, false },
        { FW_HEADER_AT + FW_H_BASE, FW_SLOT_B_ADDR, false },
        { FW_HEADER_AT + FW_H_BASE, FW_FLASH_BASE, false },
        { FW_HEADER_AT + FW_H_LENGTH, TILE_LEN + 4u, false },
        { FW_HEADER_AT + FW_H_LENGTH, TILE_LEN - 4u, false },
        { FW_HEADER_AT + FW_H_LENGTH, TILE_LEN + 2u, false },
        { FW_HEADER_AT + FW_H_LENGTH, 0, false },
        { FW_HEADER_AT + FW_H_LENGTH, FW_IMAGE_MIN - 4u, false },
        { FW_HEADER_AT + FW_H_LENGTH, FW_IMAGE_MAX + 4u, false },
        { FW_HEADER_AT + FW_H_LENGTH, 0xFFFFFFFCu, false },
        { 0, FW_RAM_BASE, false },                      /* a stack with no room */
        { 0, FW_RAM_END + 4u, false },
        { 0, FW_RAM_END - 2u, false },
        { 0, 0x08002000u, false },
        { 0, 0xFFFFFFFFu, false },
        { 4, FW_SLOT_A_ADDR + 0x300u, false },          /* not Thumb */
        { 4, (FW_SLOT_A_ADDR + 4u) | 1u, false },       /* inside the first two words */
        { 4, (FW_SLOT_A_ADDR + TILE_LEN) | 1u, false }, /* behind the image */
        { 4, (FW_SLOT_A_ADDR + TILE_LEN - 4u) | 1u, false },    /* in the checksum */
        { 4, (FW_SLOT_B_ADDR + 0x300u) | 1u, false },   /* in the other slot */
        { 4, 0xFFFFFFFFu, false },
        { 4, 0x08000101u, false },                      /* in the bootloader */
        { TILE_LEN - 4u, 0, true },                     /* the checksum itself */
        { 0x1000u, 0x12345678u, true },                 /* a word of the program */
        { TILE_LEN - 8u, 0x12345678u, true },           /* its last word */
    };

    for (size_t i = 0; i < sizeof fault / sizeof fault[0]; i++) {
        chip_new();
        flashsim_image(image, TILE_LEN, KIND_TILE, FW_SLOT_A, 1, SIM_ACT_RUN, 9);
        flashsim_put(&chip, FW_SLOT_A, image, TILE_LEN);
        CHECK(whole(FW_SLOT_A));

        put32(image + fault[i].at, fault[i].value);
        if (!fault[i].keep_crc)
            recrc(image, TILE_LEN);
        flashsim_put(&chip, FW_SLOT_A, image, TILE_LEN);
        CHECK(!whole(FW_SLOT_A));
    }

    /* the start address may be anywhere from the third word to the last */
    chip_new();
    flashsim_image(image, TILE_LEN, KIND_TILE, FW_SLOT_A, 1, SIM_ACT_RUN, 9);
    put32(image + 4, (FW_SLOT_A_ADDR + 8u) | 1u);
    recrc(image, TILE_LEN);
    flashsim_put(&chip, FW_SLOT_A, image, TILE_LEN);
    CHECK(whole(FW_SLOT_A));
    put32(image + 4, (FW_SLOT_A_ADDR + TILE_LEN - 6u) | 1u);
    recrc(image, TILE_LEN);
    flashsim_put(&chip, FW_SLOT_A, image, TILE_LEN);
    CHECK(whole(FW_SLOT_A));

    /* every single bit of a small image is under the checksum */
    chip_new();
    flashsim_image(image, FLASHSIM_IMAGE_MIN, KIND_TILE, FW_SLOT_A, 1, SIM_ACT_RUN, 10);
    for (uint32_t bit = 0; bit < 8u * FLASHSIM_IMAGE_MIN; bit += 7) {
        image[bit / 8] ^= (uint8_t)(1u << (bit % 8));
        flashsim_put(&chip, FW_SLOT_A, image, FLASHSIM_IMAGE_MIN);
        CHECK(!whole(FW_SLOT_A));
        image[bit / 8] ^= (uint8_t)(1u << (bit % 8));
    }
    flashsim_put(&chip, FW_SLOT_A, image, FLASHSIM_IMAGE_MIN);
    CHECK(whole(FW_SLOT_A));
}

static void test_slot_state(void)
{
    uint8_t *t = chip.mem + (FW_SLOT_B_ADDR - FW_FLASH_BASE) + FW_SLOT_LEN - FW_TRAILER_LEN;

    chip_new();
    CHECK_EQ(fw_slot_state(chip.mem, FW_SLOT_A), FW_NONE);
    CHECK_EQ(fw_slot_state(chip.mem, FW_SLOT_B), FW_NONE);
    CHECK_EQ(fw_slot_seq(chip.mem, FW_SLOT_B), 0);

    for (uint8_t state = FW_NONE; state <= FW_PARTIAL; state++) {
        flashsim_trailer(&chip, FW_SLOT_B, state, 41);
        CHECK_EQ(fw_slot_state(chip.mem, FW_SLOT_B), state);
        CHECK_EQ(fw_slot_seq(chip.mem, FW_SLOT_B), state == FW_NONE || state == FW_PARTIAL ? 0 : 41);
        CHECK_EQ(fw_slot_state(chip.mem, FW_SLOT_A), FW_NONE);     /* the other is its own */
    }

    /* the trailer is the slot's last 16 bytes */
    flashsim_trailer(&chip, FW_SLOT_B, FW_GOOD, 41);
    CHECK_EQ(get32(chip.mem + (FW_SLOTS_END - FW_FLASH_BASE) - 16), FW_TRAILER_MAGIC);
    flashsim_trailer(&chip, FW_SLOT_A, FW_NEW, 3);
    CHECK_EQ(get32(chip.mem + (FW_SLOT_B_ADDR - FW_FLASH_BASE) - 16), FW_TRAILER_MAGIC);

    /* a number without the magic, or with half of it: an image on its way */
    flashsim_trailer(&chip, FW_SLOT_B, FW_NONE, 0);
    put32(t + FW_T_SEQ, 9);
    CHECK_EQ(fw_slot_state(chip.mem, FW_SLOT_B), FW_PARTIAL);
    CHECK_EQ(fw_slot_seq(chip.mem, FW_SLOT_B), 0);
    put32(t + FW_T_MAGIC, FW_TRAILER_MAGIC | 0x00010000u);      /* half written */
    CHECK_EQ(fw_slot_state(chip.mem, FW_SLOT_B), FW_PARTIAL);
    /* and so is anything else that is not an erased trailer */
    for (uint32_t i = 0; i < FW_TRAILER_LEN; i++) {
        flashsim_trailer(&chip, FW_SLOT_B, FW_NONE, 0);
        t[i] = 0xFE;
        CHECK_EQ(fw_slot_state(chip.mem, FW_SLOT_B), FW_PARTIAL);
    }

    /* a mark that was cut short is a mark */
    flashsim_trailer(&chip, FW_SLOT_B, FW_NEW, 9);
    put32(t + FW_T_TRIED, 0xFFFF7FFFu);
    CHECK_EQ(fw_slot_state(chip.mem, FW_SLOT_B), FW_TRIED);
    put32(t + FW_T_OK, 0xFEFFFFFFu);
    CHECK_EQ(fw_slot_state(chip.mem, FW_SLOT_B), FW_GOOD);

    CHECK_EQ(fw_slot_of(FW_SLOT_A_ADDR), FW_SLOT_A);
    CHECK_EQ(fw_slot_of(FW_SLOT_B_ADDR), FW_SLOT_B);
    CHECK_EQ(fw_slot_of(FW_FLASH_BASE), FW_NO_SLOT);
    CHECK_EQ(fw_slot_addr(FW_SLOT_A), 0x08002000u);
    CHECK_EQ(fw_slot_addr(FW_SLOT_B), 0x08020800u);
    CHECK_EQ(FW_SLOTS_END, 0x0803F000u);
    CHECK_EQ(FW_IMAGE_MAX % FW_SECTOR, 0);
}

/* -------------------------------------------------------- the bootloader --- */

/* The rule once more, written another way: the higher of (rank of the
 * state, age, slot A before B) among the slots with a whole image that was
 * not cut short on its way in. */
static uint8_t boot_rule(const bool has[2], const uint8_t state[2], const uint32_t seq[2])
{
    static const int rank[] = { [FW_NEW] = 3, [FW_GOOD] = 2, [FW_NONE] = 1, [FW_TRIED] = 0 };
    long long best = -1;
    uint8_t slot = FW_NO_SLOT;

    for (uint8_t s = 0; s < 2; s++) {
        long long score;

        if (!has[s] || state[s] == FW_PARTIAL)
            continue;
        score = ((long long)rank[state[s]] << 40)
                + ((long long)(state[s] == FW_NONE ? 0 : seq[s]) << 1) + (s == FW_SLOT_A);
        if (score > best) {
            best = score;
            slot = s;
        }
    }
    return slot;
}

static void test_boot_every_case(void)
{
    static const uint32_t seqs[] = { 1, 2, 7 };
    static uint8_t before[FLASHSIM_LEN];
    unsigned cases = 0;

    chip_new();
    for (unsigned c = 0; c < 2u * 2u * 5u * 5u * 3u * 3u; c++) {
        bool has[2] = { c & 1u, (c >> 1) & 1u }, trial = false;
        uint8_t state[2] = { (uint8_t)((c / 4u) % 5u), (uint8_t)((c / 20u) % 5u) };
        uint32_t seq[2] = { seqs[(c / 100u) % 3u], seqs[(c / 300u) % 3u] };
        uint8_t want, got;

        for (uint8_t s = 0; s < 2; s++) {
            flashsim_image(image, FLASHSIM_IMAGE_MIN, KIND_TILE, s, 1, SIM_ACT_RUN, 20 + c);
            if (!has[s])
                image[0x280] ^= 1;              /* a bit fell over */
            flashsim_put(&chip, s, image, FLASHSIM_IMAGE_MIN);
            flashsim_trailer(&chip, s, state[s], seq[s]);
        }
        memcpy(before, chip.mem, sizeof before);

        want = boot_rule(has, state, seq);
        got = boot(&trial);
        CHECK_EQ(got, want);
        cases++;

        /* a new image was marked, and that is all the bootloader wrote */
        if (got != FW_NO_SLOT && state[got] == FW_NEW) {
            CHECK(trial);
            CHECK_EQ(fw_slot_state(chip.mem, got), FW_TRIED);
            CHECK_EQ(fw_slot_seq(chip.mem, got), seq[got]);
            flashsim_trailer(&chip, got, FW_NEW, seq[got]);
        } else {
            CHECK(!trial);
        }
        CHECK(memcmp(before, chip.mem, sizeof before) == 0);
        CHECK_EQ(chip.erases, 0);
    }
    CHECK_EQ(cases, 900);
    check_clean();
}

static void test_boot_cases(void)
{
    bool trial;

    /* an empty chip */
    chip_new();
    CHECK_EQ(boot(&trial), FW_NO_SLOT);
    CHECK(!trial);

    /* from the bench */
    chip_factory();
    CHECK_EQ(boot(&trial), FW_SLOT_A);
    CHECK(!trial);

    /* the new image gets exactly one start */
    flashsim_image(image, TILE_LEN, KIND_TILE, FW_SLOT_B, 2, SIM_ACT_RUN, 2);
    flashsim_put(&chip, FW_SLOT_B, image, TILE_LEN);
    flashsim_trailer(&chip, FW_SLOT_B, FW_NEW, 1);
    CHECK_EQ(boot(&trial), FW_SLOT_B);
    CHECK(trial);
    CHECK_EQ(boot(&trial), FW_SLOT_A);
    CHECK(!trial);
    CHECK_EQ(boot(&trial), FW_SLOT_A);

    /* confirmed, it is the one from then on */
    flashsim_trailer(&chip, FW_SLOT_B, FW_GOOD, 1);
    CHECK_EQ(boot(&trial), FW_SLOT_B);
    CHECK(!trial);

    /* the mark cannot be written: no start at all */
    flashsim_trailer(&chip, FW_SLOT_B, FW_NEW, 1);
    chip.fail_at = 1;
    CHECK_EQ(boot(&trial), FW_SLOT_A);
    CHECK(!trial);
    /* ... and what the failed write left there is a mark */
    CHECK_EQ(fw_slot_state(chip.mem, FW_SLOT_B), FW_TRIED);
    CHECK_EQ(boot(&trial), FW_SLOT_A);

    /* a flash that cannot be written at all */
    flashsim_trailer(&chip, FW_SLOT_B, FW_NEW, 1);
    flash.program = NULL;
    CHECK_EQ(boot(&trial), FW_SLOT_A);
    CHECK(!trial);
    flash = flashsim_flash(&chip);

    /* the only program there is, is started whatever its trailer says */
    chip_new();
    flashsim_image(image, TILE_LEN, KIND_TILE, FW_SLOT_B, 2, SIM_ACT_RUN, 2);
    flashsim_put(&chip, FW_SLOT_B, image, TILE_LEN);
    flashsim_trailer(&chip, FW_SLOT_B, FW_TRIED, 4);
    CHECK_EQ(boot(&trial), FW_SLOT_B);
    CHECK(!trial);
    check_clean();
}

/* ------------------------------------------------- the running program --- */

static void test_update_rounds(void)
{
    uint8_t info[FW_INFO_LEN];
    uint8_t runs = FW_SLOT_A;
    uint32_t crc_now, crc_new;
    bool trial;
    fw_t fw;

    chip_factory();
    crc_now = get32(image + TILE_LEN - 4);
    CHECK_EQ(boot(&trial), FW_SLOT_A);

    for (unsigned round = 1; round <= 5; round++) {
        uint8_t to = (uint8_t)(1u - runs);
        uint32_t length = TILE_LEN + 400u * round;

        fw_init(&fw, &flash, KIND_TILE, runs);
        CHECK(!fw.trial);
        fw_info(&fw, info);
        CHECK_EQ(info[0], runs);
        CHECK_EQ(info[1], round == 1 ? FW_NONE : FW_GOOD);
        CHECK_EQ(get32(info + 6), crc_now);
        CHECK_EQ(info[3] & (FW_F_OPEN | FW_F_STAGED), 0);

        flashsim_image(image, length, KIND_TILE, to, (uint16_t)(1 + round), SIM_ACT_RUN,
                       100 + round);
        crc_new = get32(image + length - 4);
        CHECK_EQ(send(&fw, image, length), ST_OK);
        CHECK(fw.staged);
        CHECK(whole(to));
        CHECK(whole(runs));
        CHECK_EQ(fw_slot_state(chip.mem, to), FW_NEW);
        CHECK_EQ(fw_slot_seq(chip.mem, to), round);
        fw_info(&fw, info);
        CHECK_EQ(info[2], FW_NEW);
        CHECK_EQ(info[3], FW_F_OTHER_VALID | FW_F_STAGED);
        CHECK_EQ(get32(info + 6), crc_now);
        CHECK_EQ(get32(info + 10), crc_new);

        /* the restart: the new one, on trial */
        CHECK_EQ(boot(&trial), to);
        CHECK(trial);
        fw_init(&fw, &flash, KIND_TILE, to);
        CHECK(fw.trial);
        fw_info(&fw, info);
        CHECK_EQ(info[0], to);
        CHECK_EQ(info[1], FW_TRIED);
        CHECK_EQ(get16(info + 4), 1 + round);
        CHECK_EQ(get32(info + 6), crc_new);
        CHECK_EQ(get32(info + 10), crc_now);
        CHECK_EQ(info[3], FW_F_OTHER_VALID);

        /* on trial it takes no further image: the old one is the way back */
        CHECK_EQ(fw_begin(&fw, TILE_LEN), ST_BUSY);
        CHECK(whole(runs));

        CHECK_EQ(fw_confirm(&fw), ST_OK);
        CHECK(!fw.trial);
        CHECK_EQ(fw_slot_state(chip.mem, to), FW_GOOD);
        CHECK_EQ(fw_confirm(&fw), ST_OK);              /* twice does nothing */

        CHECK_EQ(boot(&trial), to);
        CHECK(!trial);
        runs = to;
        crc_now = crc_new;
    }
    check_clean();
}

static void test_rollback(void)
{
    uint32_t crc_old;
    bool trial;
    fw_t fw;

    chip_factory();
    crc_old = get32(image + TILE_LEN - 4);
    fw_init(&fw, &flash, KIND_TILE, FW_SLOT_A);
    flashsim_image(other_image, TILE_LEN, KIND_TILE, FW_SLOT_B, 2, SIM_ACT_HANG, 3);
    CHECK_EQ(send(&fw, other_image, TILE_LEN), ST_OK);

    CHECK_EQ(boot(&trial), FW_SLOT_B);
    CHECK(trial);
    /* it hangs, or nobody confirms it; the watchdog or its own clock restarts it */
    CHECK_EQ(boot(&trial), FW_SLOT_A);
    CHECK(!trial);
    CHECK_EQ(boot(&trial), FW_SLOT_A);

    /* the old program is told what happened, and takes the next image */
    fw_init(&fw, &flash, KIND_TILE, FW_SLOT_A);
    CHECK(!fw.trial);
    uint8_t info[FW_INFO_LEN];
    fw_info(&fw, info);
    CHECK_EQ(info[1], FW_NONE);
    CHECK_EQ(info[2], FW_TRIED);
    CHECK_EQ(get32(info + 6), crc_old);
    CHECK_EQ(get32(info + 10), get32(other_image + TILE_LEN - 4));

    flashsim_image(other_image, TILE_LEN + 64, KIND_TILE, FW_SLOT_B, 3, SIM_ACT_RUN, 4);
    CHECK_EQ(send(&fw, other_image, TILE_LEN + 64), ST_OK);
    CHECK_EQ(boot(&trial), FW_SLOT_B);
    CHECK(trial);
    fw_init(&fw, &flash, KIND_TILE, FW_SLOT_B);
    CHECK_EQ(fw_confirm(&fw), ST_OK);
    CHECK_EQ(boot(&trial), FW_SLOT_B);

    /* the same from a confirmed image: back to it, not to the one before */
    flashsim_image(other_image, TILE_LEN, KIND_TILE, FW_SLOT_A, 4, SIM_ACT_HANG, 5);
    CHECK_EQ(send(&fw, other_image, TILE_LEN), ST_OK);
    CHECK_EQ(fw_slot_seq(chip.mem, FW_SLOT_A), 2);
    CHECK_EQ(boot(&trial), FW_SLOT_A);
    CHECK(trial);
    CHECK_EQ(boot(&trial), FW_SLOT_B);
    CHECK(!trial);
    check_clean();
}

static void test_receive_refusals(void)
{
    uint32_t next = 77;
    fw_t fw;

    chip_factory();
    fw_init(&fw, &flash, KIND_TILE, FW_SLOT_A);
    flashsim_image(other_image, TILE_LEN, KIND_TILE, FW_SLOT_B, 2, SIM_ACT_RUN, 3);

    /* nothing before begin */
    CHECK_EQ(fw_write(&fw, 0, other_image, 48, &next), ST_FAILED);
    CHECK_EQ(next, 0);
    CHECK_EQ(fw_commit(&fw, &next), ST_FAILED);
    CHECK_EQ(chip.erases + chip.programs, 0);

    /* lengths that cannot be an image */
    CHECK_EQ(fw_begin(&fw, 0), ST_BAD_ARG);
    CHECK_EQ(fw_begin(&fw, FW_IMAGE_MIN - 4), ST_BAD_ARG);
    CHECK_EQ(fw_begin(&fw, TILE_LEN + 1), ST_BAD_ARG);
    CHECK_EQ(fw_begin(&fw, FW_IMAGE_MAX + 4), ST_BAD_ARG);
    CHECK_EQ(fw_begin(&fw, 0xFFFFFFFCu), ST_BAD_ARG);
    CHECK_EQ(chip.erases, 0);
    CHECK(!fw.open);

    CHECK_EQ(fw_begin(&fw, TILE_LEN), ST_OK);
    CHECK_EQ(chip.erases, 2);
    CHECK_EQ(chip.programs, 1);                 /* the trailer's number */
    CHECK_EQ(fw_slot_state(chip.mem, FW_SLOT_B), FW_PARTIAL);

    /* writes come in order, in whole words, and stay inside the image */
    CHECK_EQ(fw_write(&fw, 48, other_image + 48, 48, &next), ST_BAD_ARG);
    CHECK_EQ(next, 0);
    CHECK_EQ(fw_write(&fw, 0, other_image, 0, &next), ST_BAD_ARG);
    CHECK_EQ(fw_write(&fw, 0, other_image, 47, &next), ST_BAD_ARG);
    CHECK_EQ(fw_write(&fw, 0, other_image, 48, &next), ST_OK);
    CHECK_EQ(next, 48);
    CHECK_EQ(fw_write(&fw, 0, other_image, 48, &next), ST_BAD_ARG);    /* once more */
    CHECK_EQ(next, 48);
    CHECK_EQ(fw_write(&fw, 96, other_image + 96, 48, &next), ST_BAD_ARG);
    CHECK_EQ(next, 48);
    CHECK_EQ(fw_write(&fw, 4, other_image + 4, 48, &next), ST_BAD_ARG);
    CHECK_EQ(chip.programs, 2);

    /* not complete yet */
    CHECK_EQ(fw_commit(&fw, &next), ST_BAD_ARG);
    CHECK_EQ(next, 48);
    CHECK(fw.open);

    for (uint32_t at = 48; at < TILE_LEN - 48; at += 48)
        CHECK_EQ(fw_write(&fw, at, other_image + at, 48, &next), ST_OK);
    CHECK_EQ(next, TILE_LEN - 48);
    /* not past the end that begin named */
    CHECK_EQ(fw_write(&fw, next, other_image + next, 52, &next), ST_BAD_ARG);
    CHECK_EQ(fw_write(&fw, next, other_image + next, 48, &next), ST_OK);
    CHECK_EQ(next, TILE_LEN);
    CHECK_EQ(fw_write(&fw, next, other_image, 4, &next), ST_BAD_ARG);
    CHECK_EQ(fw_commit(&fw, &next), ST_OK);
    CHECK_EQ(fw_commit(&fw, &next), ST_OK);            /* said twice */
    CHECK_EQ(fw_slot_state(chip.mem, FW_SLOT_B), FW_NEW);
    CHECK_EQ(fw_write(&fw, 0, other_image, 48, &next), ST_FAILED);
    check_clean();
}

/* What commit turns away. Each leaves the other slot as it was while the
 * image came in: nothing to start. */
static void test_commit_checks(void)
{
    bool trial;
    fw_t fw;

    /* a hub's program for a tile */
    chip_factory();
    fw_init(&fw, &flash, KIND_TILE, FW_SLOT_A);
    flashsim_image(other_image, TILE_LEN, KIND_HUB, FW_SLOT_B, 2, SIM_ACT_RUN, 3);
    CHECK_EQ(send(&fw, other_image, TILE_LEN), ST_FAILED);
    CHECK_EQ(fw_slot_state(chip.mem, FW_SLOT_B), FW_PARTIAL);
    CHECK(!fw.staged);
    /* (and a tile's for a hub) */
    fw_init(&fw, &flash, KIND_HUB, FW_SLOT_A);
    flashsim_image(other_image, TILE_LEN, KIND_TILE, FW_SLOT_B, 2, SIM_ACT_RUN, 3);
    CHECK_EQ(send(&fw, other_image, TILE_LEN), ST_FAILED);

    /* the image linked for the slot the program runs from */
    fw_init(&fw, &flash, KIND_TILE, FW_SLOT_A);
    flashsim_image(other_image, TILE_LEN, KIND_TILE, FW_SLOT_A, 2, SIM_ACT_RUN, 3);
    CHECK_EQ(send(&fw, other_image, TILE_LEN), ST_FAILED);
    CHECK(!whole(FW_SLOT_B));

    /* a byte that changed on the way */
    flashsim_image(other_image, TILE_LEN, KIND_TILE, FW_SLOT_B, 2, SIM_ACT_RUN, 3);
    other_image[5000] ^= 0x10;
    CHECK_EQ(send(&fw, other_image, TILE_LEN), ST_FAILED);

    /* a length other than the image's own: its tail missing, or too much */
    flashsim_image(other_image, TILE_LEN, KIND_TILE, FW_SLOT_B, 2, SIM_ACT_RUN, 3);
    CHECK_EQ(send(&fw, other_image, TILE_LEN - 48), ST_FAILED);
    CHECK_EQ(send(&fw, other_image, TILE_LEN + 48), ST_FAILED);

    /* through all of that the chip starts what it started before */
    CHECK_EQ(fw_slot_state(chip.mem, FW_SLOT_B), FW_PARTIAL);
    CHECK_EQ(boot(&trial), FW_SLOT_A);
    CHECK(!trial);

    /* and then takes the right one */
    CHECK_EQ(send(&fw, other_image, TILE_LEN), ST_OK);
    CHECK_EQ(boot(&trial), FW_SLOT_B);
    check_clean();
}

static void test_no_flash(void)
{
    uint8_t info[FW_INFO_LEN];
    uint32_t next;
    fw_t fw;

    chip_factory();
    fw_init(&fw, NULL, KIND_TILE, FW_SLOT_A);
    CHECK_EQ(fw.slot, FW_NO_SLOT);
    CHECK_EQ(fw_begin(&fw, TILE_LEN), ST_UNKNOWN);
    CHECK_EQ(fw_write(&fw, 0, image, 48, &next), ST_UNKNOWN);
    CHECK_EQ(fw_commit(&fw, &next), ST_UNKNOWN);
    CHECK_EQ(fw_confirm(&fw), ST_UNKNOWN);
    fw_info(&fw, info);
    CHECK_EQ(info[0], 0xFF);
    CHECK(!fw.trial);

    /* a program that does not run from a slot */
    fw_init(&fw, &flash, KIND_TILE, FW_NO_SLOT);
    CHECK_EQ(fw_begin(&fw, TILE_LEN), ST_UNKNOWN);
    fw_init(&fw, &flash, KIND_TILE, fw_slot_of(FW_FLASH_BASE));
    CHECK_EQ(fw_begin(&fw, TILE_LEN), ST_UNKNOWN);
    CHECK_EQ(chip.erases + chip.programs, 0);
}

/* The largest image there can be fills its slot up to the trailer's sector
 * and touches nothing outside it. */
static void test_largest_image(void)
{
    static uint8_t before[FLASHSIM_LEN];
    uint32_t a = FW_SLOT_A_ADDR - FW_FLASH_BASE, b = FW_SLOT_B_ADDR - FW_FLASH_BASE;
    bool trial;
    fw_t fw;

    chip_new();
    flashsim_image(image, FW_IMAGE_MAX, KIND_HUB, FW_SLOT_B, 1, SIM_ACT_RUN, 1);
    flashsim_put(&chip, FW_SLOT_B, image, FW_IMAGE_MAX);
    flashsim_trailer(&chip, FW_SLOT_B, FW_GOOD, 8);
    /* something in every place the update must leave alone */
    memset(chip.mem, 0x5A, FW_BOOT_LEN);
    memset(chip.mem + (FW_SLOTS_END - FW_FLASH_BASE), 0xA5, FLASHSIM_LEN - (FW_SLOTS_END - FW_FLASH_BASE));
    memcpy(before, chip.mem, sizeof before);

    fw_init(&fw, &flash, KIND_HUB, FW_SLOT_B);
    flashsim_image(other_image, FW_IMAGE_MAX, KIND_HUB, FW_SLOT_A, 2, SIM_ACT_RUN, 2);
    CHECK_EQ(send(&fw, other_image, FW_IMAGE_MAX), ST_OK);
    CHECK_EQ(chip.erases, FW_SLOT_LEN / FW_SECTOR);     /* each sector once */
    CHECK(memcmp(chip.mem + a, other_image, FW_IMAGE_MAX) == 0);
    CHECK(memcmp(chip.mem, before, a) == 0);
    CHECK(memcmp(chip.mem + b, before + b, FLASHSIM_LEN - b) == 0);
    CHECK_EQ(fw_slot_seq(chip.mem, FW_SLOT_A), 9);
    CHECK_EQ(boot(&trial), FW_SLOT_A);
    CHECK(trial);
    check_clean();
}

/* --------------------------------------------------------- flash faults --- */

static void test_flash_fails(void)
{
    uint32_t next;
    bool trial;
    fw_t fw;

    chip_factory();
    fw_init(&fw, &flash, KIND_TILE, FW_SLOT_A);
    flashsim_image(other_image, TILE_LEN, KIND_TILE, FW_SLOT_B, 2, SIM_ACT_RUN, 3);

    /* an erase in begin, or the trailer's number: no transfer; begin once more */
    for (unsigned n = 1; n <= 3; n++) {
        chip.fail_at = n;
        CHECK_EQ(fw_begin(&fw, TILE_LEN), ST_FAILED);
        CHECK(!fw.open);
        CHECK_EQ(fw_write(&fw, 0, other_image, 48, &next), ST_FAILED);
    }
    CHECK_EQ(fw_begin(&fw, TILE_LEN), ST_OK);

    /* an erase on the way: the same write once more does it */
    for (next = 0; next < FW_SECTOR - 32u;)
        CHECK_EQ(fw_write(&fw, next, other_image + next, 48, &next), ST_OK);
    chip.fail_at = 1;
    CHECK_EQ(fw_write(&fw, next, other_image + next, 48, &next), ST_FAILED);
    CHECK(fw.open);
    CHECK_EQ(next, 2016);
    CHECK_EQ(fw_write(&fw, next, other_image + next, 48, &next), ST_OK);

    /* a write: what is in the flash now cannot be written over. From begin. */
    chip.fail_at = 1;
    CHECK_EQ(fw_write(&fw, next, other_image + next, 48, &next), ST_FAILED);
    CHECK(!fw.open);
    CHECK_EQ(next, 0);
    CHECK_EQ(fw_commit(&fw, &next), ST_FAILED);
    CHECK_EQ(boot(&trial), FW_SLOT_A);

    /* a flash that says yes and holds something else */
    CHECK_EQ(fw_begin(&fw, TILE_LEN), ST_OK);
    CHECK_EQ(fw_write(&fw, 0, other_image, 48, &next), ST_OK);
    chip.mem[(FW_SLOT_B_ADDR - FW_FLASH_BASE) + 48 + 5] = 0xFE;     /* a weak bit, written already */
    CHECK_EQ(fw_write(&fw, 48, other_image + 48, 48, &next), ST_FAILED);
    CHECK_EQ(chip.rewrites, 1);
    chip.rewrites = 0;

    /* the trailer's magic: a whole image that is never started */
    CHECK_EQ(fw_begin(&fw, TILE_LEN), ST_OK);
    for (next = 0; next < TILE_LEN;)
        CHECK_EQ(fw_write(&fw, next, other_image + next, 48, &next), ST_OK);
    chip.fail_at = 1;
    CHECK_EQ(fw_commit(&fw, &next), ST_FAILED);
    CHECK(!fw.staged);
    CHECK(!fw.open);
    CHECK(whole(FW_SLOT_B));
    CHECK_EQ(fw_slot_state(chip.mem, FW_SLOT_B), FW_PARTIAL);
    CHECK_EQ(boot(&trial), FW_SLOT_A);
    CHECK(!trial);

    /* confirm */
    CHECK_EQ(send(&fw, other_image, TILE_LEN), ST_OK);
    CHECK_EQ(boot(&trial), FW_SLOT_B);
    fw_init(&fw, &flash, KIND_TILE, FW_SLOT_B);
    flash.program = NULL;
    fw.flash.program = NULL;
    CHECK_EQ(fw_confirm(&fw), ST_FAILED);
    CHECK(fw.trial);
    flash = flashsim_flash(&chip);
    fw.flash = flash;
    CHECK_EQ(fw_confirm(&fw), ST_OK);
    CHECK(!fw.trial);
    check_clean();
}

/* The power goes during the n-th flash call of an update, for every n. The
 * chip must then start the program it had, not on trial; only an update
 * that got through to its last write starts the new one, on trial. */
static void cut_everywhere(uint8_t runs, uint8_t state, uint32_t length)
{
    uint8_t to = (uint8_t)(1u - runs);
    unsigned calls = 0, went_through = 0;

    for (unsigned n = 1; went_through == 0; n++) {
        uint8_t st;
        bool trial;
        fw_t fw;

        chip_new();
        flashsim_image(image, TILE_LEN, KIND_TILE, runs, 1, SIM_ACT_RUN, 1);
        flashsim_put(&chip, runs, image, TILE_LEN);
        flashsim_trailer(&chip, runs, state, 6);
        /* and where the new one goes, a whole program that the bootloader
         * would not start: the one before, or one that failed its trial */
        flashsim_image(other_image, length, KIND_TILE, to, 1, SIM_ACT_RUN, 2);
        flashsim_put(&chip, to, other_image, length);
        flashsim_trailer(&chip, to, state == FW_GOOD ? FW_GOOD : FW_TRIED, 5);
        CHECK_EQ(boot(&trial), runs);
        CHECK(!trial);

        fw_init(&fw, &flash, KIND_TILE, runs);
        flashsim_image(other_image, length, KIND_TILE, to, 2, SIM_ACT_RUN, 3);
        chip.fail_at = n;
        st = send(&fw, other_image, length);
        if (chip.fail_at == 0 && st != ST_OK) {
            /* the n-th call was cut short */
            CHECK_EQ(st, ST_FAILED);
            CHECK_EQ(boot(&trial), runs);
            CHECK(!trial);
            CHECK(whole(runs));
            CHECK_EQ(fw_slot_state(chip.mem, runs), state);
            calls++;
        } else {
            CHECK_EQ(st, ST_OK);
            chip.fail_at = 0;
            CHECK_EQ(boot(&trial), to);
            CHECK(trial);
            went_through = n;
        }
        check_clean();
    }
    /* in begin two erases and the trailer's number, then an erase per
     * further sector and a write per 48 bytes, at last the trailer's magic */
    CHECK_EQ(calls, 2 + (length - 1) / FW_SECTOR + (length + 47) / 48 + 2);
    CHECK_EQ(went_through, calls + 1);
}

static void test_power_cut_anywhere(void)
{
    cut_everywhere(FW_SLOT_A, FW_NONE, 4800);
    cut_everywhere(FW_SLOT_A, FW_GOOD, 4800);
    cut_everywhere(FW_SLOT_B, FW_GOOD, 4096 + 480);
    cut_everywhere(FW_SLOT_B, FW_NONE, FLASHSIM_IMAGE_MIN);
}

/* The power goes while the bootloader marks the new image, and while the
 * new program confirms. */
static void test_power_cut_marks(void)
{
    bool trial;
    fw_t fw;

    chip_factory();
    fw_init(&fw, &flash, KIND_TILE, FW_SLOT_A);
    flashsim_image(other_image, TILE_LEN, KIND_TILE, FW_SLOT_B, 2, SIM_ACT_RUN, 3);
    CHECK_EQ(send(&fw, other_image, TILE_LEN), ST_OK);

    /* during the mark: the write was begun, the chip never got to the jump */
    CHECK_EQ(boot(&trial), FW_SLOT_B);
    flashsim_cut(&chip);
    CHECK_EQ(fw_slot_state(chip.mem, FW_SLOT_B), FW_TRIED);
    CHECK_EQ(boot(&trial), FW_SLOT_A);         /* the new one lost its start; never a second */
    CHECK(!trial);

    /* during confirm: either it counts or it does not, and both are a
     * program that works */
    fw_init(&fw, &flash, KIND_TILE, FW_SLOT_A);
    CHECK_EQ(send(&fw, other_image, TILE_LEN), ST_OK);
    CHECK_EQ(boot(&trial), FW_SLOT_B);
    fw_init(&fw, &flash, KIND_TILE, FW_SLOT_B);
    CHECK_EQ(fw_confirm(&fw), ST_OK);
    flashsim_cut(&chip);
    CHECK_EQ(fw_slot_state(chip.mem, FW_SLOT_B), FW_GOOD);
    CHECK_EQ(boot(&trial), FW_SLOT_B);
    CHECK(!trial);
    check_clean();
}

int main(void)
{
    RUN(test_image_check);
    RUN(test_image_faults);
    RUN(test_slot_state);
    RUN(test_boot_every_case);
    RUN(test_boot_cases);
    RUN(test_update_rounds);
    RUN(test_rollback);
    RUN(test_receive_refusals);
    RUN(test_commit_checks);
    RUN(test_no_flash);
    RUN(test_largest_image);
    RUN(test_flash_fails);
    RUN(test_power_cut_anywhere);
    RUN(test_power_cut_marks);
    return T_DONE();
}
