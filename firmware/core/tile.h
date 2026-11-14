/* The tile program, minus the hardware: a node, its keys, and what the two
 * say to the hub (NOTE, STATE, INFO, POS, RAW) and do for it (CMD).
 *
 * The port code, or the simulator, calls:
 *   tile_rx()     with bytes a port received,
 *   tile_scan()   with each finished ADC scan,
 *   tile_tick()   about once a millisecond,
 *   tile_tx()     when a port's transmitter is free,
 *   tile_actions() after tile_tick(), for the things only hardware can do. */
#ifndef PIANO_TILE_H
#define PIANO_TILE_H

#include "keys.h"
#include "node.h"
#include "store.h"

/* What tile_actions() can ask for. */
#define TILE_ACT_SAVE        0x01u  /* write tile_save_image() to flash, then tile_save_done() */
#define TILE_ACT_REBOOT      0x02u
#define TILE_ACT_BOOTLOADER  0x04u  /* restart into the ROM bootloader */

#define TILE_REPLY_MAX       6      /* counter, status, up to 4 bytes of data */

typedef struct {
    node_t node;
    keys_t keys;
    uint32_t now;               /* the time of the call in progress, for the note callback */
    uint32_t started;

    uint8_t events;             /* NOTE frames made so far */
    uint32_t next_state, next_info, next_pos, next_raw;

    bool raw;                   /* RAW frames are wanted */
    uint8_t raw_scan;           /* counts the scans sent as RAW */
    uint16_t adc[KEYS_N];       /* the last scan */
    uint8_t depth_sent[KEYS_N]; /* what the hub was last told */
    uint8_t pos_from;           /* where the next POS starts looking, so no key starves */

    /* commands */
    uint8_t epoch;              /* node.epoch when the counters below were last good */
    bool have_cmd, have_bcast;
    uint8_t last_cmd, last_bcast;       /* counter of the last command run: aimed here, or for all */
    uint8_t reply[TILE_REPLY_MAX];      /* the reply to last_cmd, to send again on a repeat */
    uint8_t reply_len;                  /* 0: none (yet) */
    bool saving;                /* a SAVE waits for the flash */
    bool save_reply;            /* ... and its sender for a reply, */
    uint8_t save_counter;       /* ... with this counter */

    uint8_t actions;            /* TILE_ACT_ bits not yet collected */
    uint8_t later;              /* TILE_ACT_REBOOT or _BOOTLOADER, due at later_at */
    uint32_t later_at;
} tile_t;

/* uid: the chip's 96-bit ID. record: what flash holds (may be NULL or
 * rubbish: then defaults). */
void tile_init(tile_t *t, const uint8_t uid[12], const uint8_t *record, size_t record_len,
               uint32_t scan_hz, uint32_t now);

void tile_rx(tile_t *t, uint8_t port, const uint8_t *data, size_t len, uint32_t now);
void tile_scan(tile_t *t, uint32_t scan_no, const uint16_t adc[KEYS_N], uint32_t now);
void tile_tick(tile_t *t, uint32_t now);

static inline size_t tile_tx(tile_t *t, uint8_t port, uint8_t *wire)
{
    return node_tx(&t->node, port, wire);
}

static inline bool tile_swapped(const tile_t *t, uint8_t port)
{
    return node_swapped(&t->node, port);
}

/* The TILE_ACT_ bits that came up since the last call. */
unsigned tile_actions(tile_t *t);

/* For TILE_ACT_SAVE: the record to write, and how the writing went. */
void tile_save_image(const tile_t *t, uint8_t out[STORE_TILE_LEN]);
void tile_save_done(tile_t *t, bool ok);

#endif
