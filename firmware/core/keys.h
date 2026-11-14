/* From ADC values to notes: DESIGN.md, "Keys".
 *
 * keys_scan() takes one finished scan (40 values in key-index order, key
 * index = 5 * column + row) and calls back for every note that starts or
 * stops. Everything is integer arithmetic. */
#ifndef PIANO_KEYS_H
#define PIANO_KEYS_H

#include "proto.h"

#define KEYS_N       40
#define KEYS_COLS    8
#define KEYS_ROWS    5
#define KEYS_WARMUP  64         /* scans averaged for the first rest value */

/* Tile parameters, by the numbers GET and SET use. */
enum {
    PARAM_LO = 1,               /* thousandths of travel */
    PARAM_HI = 2,
    PARAM_RELEASE = 3,
    PARAM_RETRIGGER = 4,
    PARAM_POS_MODE = 5,         /* 0 off, 1 past `hi` of keys that are on, 2 every key */
    PARAM_POS_INTERVAL = 6,     /* ms */
    PARAM_RAW_INTERVAL = 7,     /* ms */
    PARAM_SWING = 8,            /* ADC counts from rest to bottom when nothing was measured;
                                   negative if the sensor's output falls as the key goes down */
    TILE_PARAMS = 8,
};

enum { KEY_IDLE, KEY_DOWN, KEY_HELD, KEY_UP };

/* One key's calibration as flash keeps it: ADC values. rest == bottom means
 * the key was never measured. */
typedef struct {
    uint16_t rest;
    uint16_t bottom;
} key_cal_t;

typedef void (*keys_note_fn)(void *ctx, uint8_t key, bool on, uint16_t speed);

typedef struct {
    int32_t f;          /* filtered ADC value, x 16 */
    int32_t rest;       /* ADC value with the key up, x 65536 */
    int32_t swing;      /* bottom - rest, x 16. Its sign is the sensor's direction. Never 0. */
    int32_t far_lo;     /* while calibrating: the lowest and highest f seen */
    int32_t far_hi;
    int32_t t_mark;     /* 1/256 scans since the crossing the state remembers */
    int32_t t_turn;     /* UP: 1/256 scans since x was lowest */
    uint32_t warm_sum;  /* sum of the first scans */
    uint16_t x;         /* travel now: 0 up, 65535 down */
    uint16_t low;       /* UP: the lowest x since release */
    uint8_t state;
    bool measured;      /* swing comes from flash or a calibration */
} key_t;

typedef struct {
    key_t key[KEYS_N];
    int32_t param[TILE_PARAMS + 1];     /* by PARAM_ number; [0] unused */
    uint16_t lo, lo_exit, hi, release, retrigger;   /* the levels, in x units */
    uint32_t scan_hz;
    uint32_t last_scan;
    uint16_t warm;      /* warm-up scans still to come */
    bool started;       /* a first scan has been seen */
    bool calibrating;
    keys_note_fn note;
    void *ctx;
} keys_t;

void keys_default_params(int32_t param[TILE_PARAMS + 1]);

/* Is `value` allowed for parameter `id`, given the other values in `param`? */
bool keys_param_ok(const int32_t param[TILE_PARAMS + 1], uint8_t id, int32_t value);

void keys_init(keys_t *k, uint32_t scan_hz, keys_note_fn note, void *ctx);

/* Replace all parameters (from flash). If any value is not allowed, none is
 * taken and the answer is false. */
bool keys_load_params(keys_t *k, const int32_t param[TILE_PARAMS + 1]);

bool keys_set_param(keys_t *k, uint8_t id, int32_t value);

/* Measured swings, to and from the flash record. */
void keys_load_cal(keys_t *k, const key_cal_t cal[KEYS_N]);
void keys_get_cal(const keys_t *k, key_cal_t cal[KEYS_N]);

/* CAL_START, CAL_STOP or CAL_FORGET. Stop returns how many keys were pressed
 * far enough to be measured; the others return 0. */
uint8_t keys_cal(keys_t *k, uint8_t what);

/* How many keys have a measured swing. */
uint8_t keys_measured(const keys_t *k);

/* One scan. scan_no counts scans as the hardware made them; a gap in the
 * numbers means scans were lost, and the time between is still right. */
void keys_scan(keys_t *k, uint32_t scan_no, const uint16_t adc[KEYS_N]);

/* False during the first KEYS_WARMUP scans. */
static inline bool keys_ready(const keys_t *k)
{
    return k->started && k->warm == 0;
}

/* Is the key on: a NOTE on was sent and its NOTE off not yet. */
static inline bool keys_on(const keys_t *k, uint8_t key)
{
    return k->key[key].state == KEY_HELD || k->key[key].state == KEY_UP;
}

/* The STATE bitmap: key 0 is bit 0 of byte 0. */
void keys_bitmap(const keys_t *k, uint8_t out[5]);

/* A key's depth for POS, 0..255, under PARAM_POS_MODE. */
uint8_t keys_depth(const keys_t *k, uint8_t key);

#endif
