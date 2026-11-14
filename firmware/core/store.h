/* What flash keeps: one record for a tile, one for the hub.
 *
 * A record is bytes laid out by hand (little-endian, no padding), so it does
 * not depend on the compiler: magic (4), version (2), flags (2, 0 for now),
 * the contents, CRC-32 of everything before it (4). A record with the wrong
 * magic, version, length or CRC is no record, and the program starts with
 * its defaults. */
#ifndef PIANO_STORE_H
#define PIANO_STORE_H

#include "keys.h"

#define STORE_VERSION    1
#define STORE_HEAD_LEN   8
#define STORE_TILE_MAGIC 0x314C4954u    /* "TIL1" */
#define STORE_HUB_MAGIC  0x31425548u    /* "HUB1" */

/* ---- tile: parameters and calibration ---- */

typedef struct {
    int32_t param[TILE_PARAMS + 1];     /* by PARAM_ number; [0] unused */
    key_cal_t cal[KEYS_N];
} tile_record_t;

#define STORE_TILE_LEN   (STORE_HEAD_LEN + 4 * TILE_PARAMS + 4 * KEYS_N + 4)

void store_tile_pack(const tile_record_t *r, uint8_t out[STORE_TILE_LEN]);
bool store_tile_unpack(const uint8_t *in, size_t len, tile_record_t *r);

/* ---- hub: settings ---- */

typedef struct {
    int16_t base;           /* the MIDI note of lattice point (0, 0) */
    int8_t step_a, step_b;  /* semitones per lattice step */
    uint8_t channel;        /* 0..15 */
    uint8_t aftertouch;     /* 1: POS becomes polyphonic aftertouch */
    uint16_t vel1, vel127;  /* the speeds that give velocity 1 and 127 */
    int8_t bend;            /* -100..100: the velocity curve's bend */
    uint8_t pd_volts;       /* what to ask the charger for: 5, 9, 12 or 15 */
} hub_settings_t;

#define STORE_HUB_LEN    (STORE_HEAD_LEN + 12 + 4)

void store_hub_pack(const hub_settings_t *s, uint8_t out[STORE_HUB_LEN]);
bool store_hub_unpack(const uint8_t *in, size_t len, hub_settings_t *s);

#endif
