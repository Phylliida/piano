/* A chip's program flash on a PC, for core/fw.h: 256 KB that behave like
 * the real thing. A sector is erased as a whole, a word is written once
 * after that, and either can go wrong or be cut short by the power.
 *
 * Also here: program images made of filler, since no real program runs in
 * the simulator. What runs there is always this PC's tile.c or hub.c; the
 * image in the slot only decides whether it runs at all. */
#ifndef PIANO_FLASHSIM_H
#define PIANO_FLASHSIM_H

#include "fw.h"

#define FLASHSIM_LEN  (256u * 1024u)

typedef struct {
    uint8_t mem[FLASHSIM_LEN];
    uint32_t noise;             /* for what a cut-off operation leaves behind */

    unsigned erases, programs;  /* calls that were let through */
    unsigned stray;             /* calls outside the two slots, or not on whole sectors and
                                   words: refused, and a fault of the caller */
    unsigned rewrites;          /* programs onto flash that was not erased: refused */
    unsigned fail_at;           /* 0: nothing planned. n: the n-th call from now does half its
                                   work and reports failure */

    /* the last call that was let through, for flashsim_cut() */
    bool last_erase;
    uint32_t last_addr, last_len;
} flashsim_t;

void flashsim_init(flashsim_t *f, uint32_t seed);   /* all erased */
fw_flash_t flashsim_flash(flashsim_t *f);

/* The power went during the last call: it did half its work. */
void flashsim_cut(flashsim_t *f);

/* ---- images ---- */

/* How the simulator plays an image: it has "SIM!" and one of these behind
 * its header. */
#define FLASHSIM_ACT_AT  (FW_HEADER_AT + FW_HEADER_LEN)
enum {
    SIM_ACT_RUN = 0,
    SIM_ACT_HANG = 1,       /* a program that stops dead at once */
    SIM_ACT_DEAF = 2,       /* one that runs and hears nothing */
};
#define FLASHSIM_IMAGE_MIN  0x300u

/* A whole image for `slot` in out[0..length): vectors, header, filler made
 * from `seed`, CRC. length: a multiple of 4, FLASHSIM_IMAGE_MIN at least. */
void flashsim_image(uint8_t *out, uint32_t length, uint8_t kind, uint8_t slot, uint16_t version,
                    uint8_t act, uint32_t seed);
/* The act of the image in a slot; SIM_ACT_RUN for an image that says nothing. */
uint8_t flashsim_act(const flashsim_t *f, uint8_t slot);

/* Put an image into a slot the way a programmer on the debug pads would:
 * the sectors it covers are erased and written, nothing else is touched. */
void flashsim_put(flashsim_t *f, uint8_t slot, const uint8_t *image, uint32_t length);

/* Give a slot the trailer of an image in `state` (FW_NONE: an erased one). */
void flashsim_trailer(flashsim_t *f, uint8_t slot, uint8_t state, uint32_t seq);

#endif
