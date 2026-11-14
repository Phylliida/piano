#include "keys.h"

#include <string.h>

#include "lut.h"

#define ADC_MAX     4095
#define REST_FAST   256         /* rest follows a value beyond it in 1/8 s at 2 kHz ... */
#define REST_SLOW   4096        /* ... and one just short of it in 2 s */
#define REST_BAND   32          /* "just short": within 1/32 of the swing */
#define CAL_MIN     (64 * 16)   /* a calibration press must move the value by 64 counts */
#define LO_HYST     20          /* DOWN falls back to IDLE this far under `lo`, thousandths */
#define DT_MAX      2000u       /* scans; a longer gap counts as this long */
#define T_MAX       (1 << 30)

/* ------------------------------------------------------------- parameters --- */

void keys_default_params(int32_t param[TILE_PARAMS + 1])
{
    param[0] = 0;
    param[PARAM_LO] = 150;
    param[PARAM_HI] = 600;
    param[PARAM_RELEASE] = 500;
    param[PARAM_RETRIGGER] = 100;
    param[PARAM_POS_MODE] = 0;
    param[PARAM_POS_INTERVAL] = 5;
    param[PARAM_RAW_INTERVAL] = 20;
    param[PARAM_SWING] = 600;   /* a guess until a real switch is measured */
}

bool keys_param_ok(const int32_t param[TILE_PARAMS + 1], uint8_t id, int32_t value)
{
    switch (id) {
    case PARAM_LO:
        return value >= 10 && value < param[PARAM_RELEASE];
    case PARAM_HI:
        return value >= param[PARAM_RELEASE] && value <= 990;
    case PARAM_RELEASE:
        return value > param[PARAM_LO] && value <= param[PARAM_HI];
    case PARAM_RETRIGGER:
        return value >= 0 && value <= 1000;
    case PARAM_POS_MODE:
        return value >= 0 && value <= 2;
    case PARAM_POS_INTERVAL:
        return value >= 1 && value <= 1000;
    case PARAM_RAW_INTERVAL:
        return value >= 2 && value <= 1000;
    case PARAM_SWING:
        return (value >= 16 && value <= ADC_MAX) || (value <= -16 && value >= -ADC_MAX);
    default:
        return false;
    }
}

static uint16_t level(int32_t thousandths)
{
    return (uint16_t)((uint32_t)thousandths * 65535u / 1000u);
}

static void set_levels(keys_t *k)
{
    int32_t lo = k->param[PARAM_LO];

    k->lo = level(lo);
    k->lo_exit = level(lo > 2 * LO_HYST ? lo - LO_HYST : lo / 2);
    k->hi = level(k->param[PARAM_HI]);
    k->release = level(k->param[PARAM_RELEASE]);
    k->retrigger = level(k->param[PARAM_RETRIGGER]);
}

/* The swing a key has until it is measured. */
static void nominal_swing(keys_t *k, key_t *key)
{
    key->swing = k->param[PARAM_SWING] * 16;
    key->measured = false;
}

void keys_init(keys_t *k, uint32_t scan_hz, keys_note_fn note, void *ctx)
{
    memset(k, 0, sizeof *k);
    k->scan_hz = scan_hz;
    k->note = note;
    k->ctx = ctx;
    k->warm = KEYS_WARMUP;
    keys_default_params(k->param);
    set_levels(k);
    for (unsigned i = 0; i < KEYS_N; i++)
        nominal_swing(k, &k->key[i]);
}

bool keys_load_params(keys_t *k, const int32_t param[TILE_PARAMS + 1])
{
    for (uint8_t id = 1; id <= TILE_PARAMS; id++) {
        if (!keys_param_ok(param, id, param[id]))
            return false;
    }
    memcpy(k->param, param, sizeof k->param);
    k->param[0] = 0;
    set_levels(k);
    for (unsigned i = 0; i < KEYS_N; i++) {
        if (!k->key[i].measured)
            nominal_swing(k, &k->key[i]);
    }
    return true;
}

bool keys_set_param(keys_t *k, uint8_t id, int32_t value)
{
    if (!keys_param_ok(k->param, id, value))
        return false;
    k->param[id] = value;
    set_levels(k);
    if (id == PARAM_SWING) {
        for (unsigned i = 0; i < KEYS_N; i++) {
            if (!k->key[i].measured)
                nominal_swing(k, &k->key[i]);
        }
    }
    return true;
}

/* ------------------------------------------------------------ calibration --- */

void keys_load_cal(keys_t *k, const key_cal_t cal[KEYS_N])
{
    for (unsigned i = 0; i < KEYS_N; i++) {
        int32_t d = (int32_t)cal[i].bottom - (int32_t)cal[i].rest;
        key_t *key = &k->key[i];

        /* Only the difference is kept: where rest is today is measured anew
         * at every start. */
        if (cal[i].rest <= ADC_MAX && cal[i].bottom <= ADC_MAX && (d >= 16 || d <= -16)) {
            key->swing = d * 16;
            key->measured = true;
        } else {
            nominal_swing(k, key);
        }
    }
}

void keys_get_cal(const keys_t *k, key_cal_t cal[KEYS_N])
{
    for (unsigned i = 0; i < KEYS_N; i++) {
        const key_t *key = &k->key[i];
        int32_t rest = (key->rest + 32768) >> 16;
        int32_t bottom = rest + key->swing / 16;

        if (rest > ADC_MAX)
            rest = ADC_MAX;
        if (bottom < 0)
            bottom = 0;
        if (bottom > ADC_MAX)
            bottom = ADC_MAX;
        cal[i].rest = (uint16_t)rest;
        cal[i].bottom = (uint16_t)(key->measured ? bottom : rest);
    }
}

uint8_t keys_cal(keys_t *k, uint8_t what)
{
    uint8_t n = 0;

    switch (what) {
    case CAL_START:
    case CAL_FORGET:
        for (unsigned i = 0; i < KEYS_N; i++) {
            key_t *key = &k->key[i];
            nominal_swing(k, key);
            key->far_lo = key->far_hi = key->f;
        }
        k->calibrating = what == CAL_START;
        break;
    case CAL_STOP:
        if (!k->calibrating)
            break;
        for (unsigned i = 0; i < KEYS_N; i++) {
            key_t *key = &k->key[i];
            int32_t rest = key->rest >> 12;
            int32_t above = key->far_hi - rest, below = rest - key->far_lo;

            /* The bottom is whichever way the value went furthest. */
            if (above >= below && above >= CAL_MIN) {
                key->swing = above;
                key->measured = true;
            } else if (below > above && below >= CAL_MIN) {
                key->swing = -below;
                key->measured = true;
            }
            n += key->measured;
        }
        k->calibrating = false;
        break;
    default:
        break;
    }
    return n;
}

uint8_t keys_measured(const keys_t *k)
{
    uint8_t n = 0;

    for (unsigned i = 0; i < KEYS_N; i++)
        n += k->key[i].measured;
    return n;
}

/* ------------------------------------------------------------------- scan --- */

/* When x passed `at` between the last scan (0) and this one (`step`), taking
 * it to have moved in a straight line from x0 to x1. */
static int32_t crossing(int32_t step, uint16_t x0, uint16_t x1, uint16_t at)
{
    int32_t den = (int32_t)x1 - x0, num = (int32_t)at - x0;

    if (den < 0) {
        den = -den;
        num = -num;
    }
    if (num <= 0)
        return 0;
    if (num >= den)
        return step;
    return (int32_t)((int64_t)step * num / den);
}

/* Hundredths of full travel per second, for `dist` (x units) covered in
 * `dur` (1/256 scans). Never 0: the hub uses 0 for "not known". */
static uint16_t speed(const keys_t *k, uint32_t dist, int32_t dur)
{
    if (dur < 1)
        dur = 1;
    uint64_t v = (uint64_t)dist * k->scan_hz * 25u / (64u * (uint64_t)dur);
    if (v < 1)
        return 1;
    return v > 65535u ? 65535u : (uint16_t)v;
}

static void track_rest(key_t *key)
{
    int32_t d = (key->f << 12) - key->rest;             /* how far from rest, x 65536 */
    int32_t sw = key->swing < 0 ? -key->swing : key->swing;

    if (d == 0)
        return;
    if ((d > 0) != (key->swing > 0)) {
        /* Further up than "up": rest was wrong (the key was held down at
         * start, or things warmed up). Follow quickly. */
        key->rest += d / REST_FAST;
    } else {
        if (d < 0)
            d = -d;
        if (d < (sw << 12) / REST_BAND)
            key->rest += ((key->f << 12) - key->rest) / REST_SLOW;
    }
}

static void emit(keys_t *k, uint8_t i, bool on, uint16_t spd)
{
    if (k->note)
        k->note(k->ctx, i, on, spd);
}

static void key_step(keys_t *k, uint8_t i, uint16_t raw, int32_t step)
{
    key_t *key = &k->key[i];

    if (raw > ADC_MAX)
        raw = ADC_MAX;
    key->f = (key->f + (int32_t)raw * 16) / 2;

    if (k->calibrating) {
        if (key->f < key->far_lo)
            key->far_lo = key->f;
        if (key->f > key->far_hi)
            key->far_hi = key->f;
    } else if (key->state == KEY_IDLE) {
        track_rest(key);
    }

    /* Travel: how far from rest toward bottom, then through the table. */
    int32_t num = key->f - (key->rest >> 12);
    int32_t sw = key->swing;
    if (sw < 0) {
        num = -num;
        sw = -sw;
    }
    if (num < 0)
        num = 0;
    if (num > sw) {
        if (key->measured) {
            num = sw;
        } else {
            /* nobody measured this key's bottom: the furthest press so far is it */
            sw = num;
            key->swing = key->swing < 0 ? -num : num;
        }
    }
    uint16_t x0 = key->x;
    uint16_t x1 = lut_apply(lut_travel, (uint16_t)((uint32_t)num * 65535u / (uint32_t)sw));
    key->x = x1;

    /* The timers hold the time since their moment as of the last scan; this
     * scan is `step` later. */
    if (key->state == KEY_IDLE && x1 >= k->lo) {
        key->t_mark = -crossing(step, x0, x1, k->lo);
        key->state = KEY_DOWN;
    }
    if (key->state == KEY_DOWN) {
        if (x1 >= k->hi) {
            emit(k, i, true, speed(k, (uint32_t)(k->hi - k->lo),
                                   key->t_mark + crossing(step, x0, x1, k->hi)));
            key->state = KEY_HELD;
        } else if (x1 < k->lo_exit) {
            key->state = KEY_IDLE;
        }
    } else if (key->state == KEY_HELD && x1 < k->release) {
        key->t_mark = -crossing(step, x0, x1, k->release);
        key->low = x1;
        key->t_turn = -step;
        key->state = KEY_UP;
    }
    if (key->state == KEY_UP) {
        if (x1 < key->low) {
            key->low = x1;
            key->t_turn = -step;
        }
        if (x1 < k->lo) {
            emit(k, i, false, speed(k, (uint32_t)(k->release - k->lo),
                                    key->t_mark + crossing(step, x0, x1, k->lo)));
            key->state = KEY_IDLE;
        } else if (x1 >= k->hi) {
            /* Pressed again without having been let go. Far enough up in
             * between, and it is a new note that starts where the key
             * turned; otherwise the old note just goes on. */
            if (k->release - key->low >= k->retrigger) {
                emit(k, i, false, speed(k, (uint32_t)(k->release - key->low),
                                        key->t_mark - key->t_turn));
                emit(k, i, true, speed(k, (uint32_t)(k->hi - key->low),
                                       key->t_turn + crossing(step, x0, x1, k->hi)));
            }
            key->state = KEY_HELD;
        }
    }

    if (key->t_mark < T_MAX)
        key->t_mark += step;
    if (key->t_turn < T_MAX)
        key->t_turn += step;
}

void keys_scan(keys_t *k, uint32_t scan_no, const uint16_t adc[KEYS_N])
{
    uint32_t dt = 1;

    if (k->started) {
        dt = scan_no - k->last_scan;
        if (dt == 0)
            return;                     /* the same scan twice */
        if (dt > DT_MAX)
            dt = DT_MAX;
    }
    k->started = true;
    k->last_scan = scan_no;

    if (k->warm) {
        /* The first scans only say where rest is. */
        for (unsigned i = 0; i < KEYS_N; i++)
            k->key[i].warm_sum += adc[i] > ADC_MAX ? ADC_MAX : adc[i];
        if (--k->warm == 0) {
            for (unsigned i = 0; i < KEYS_N; i++) {
                key_t *key = &k->key[i];
                key->rest = (int32_t)(key->warm_sum * (65536u / KEYS_WARMUP));
                key->f = (int32_t)(key->warm_sum * 16u / KEYS_WARMUP);
                key->far_lo = key->far_hi = key->f;
            }
        }
        return;
    }

    for (uint8_t i = 0; i < KEYS_N; i++)
        key_step(k, i, adc[i], (int32_t)(dt * 256u));
}

/* ------------------------------------------------------------ for sending --- */

void keys_bitmap(const keys_t *k, uint8_t out[5])
{
    memset(out, 0, 5);
    for (uint8_t i = 0; i < KEYS_N; i++) {
        if (keys_on(k, i))
            out[i / 8] |= (uint8_t)(1u << (i % 8));
    }
}

uint8_t keys_depth(const keys_t *k, uint8_t i)
{
    const key_t *key = &k->key[i];

    switch (k->param[PARAM_POS_MODE]) {
    case 1:
        if (!keys_on(k, i) || key->x <= k->hi)
            return 0;
        /* rounded, so that a key on the floor says 255 */
        return (uint8_t)(((uint32_t)(key->x - k->hi) * 255u + (65535u - k->hi) / 2u) /
                         (uint32_t)(65535u - k->hi));
    case 2:
        return (uint8_t)(key->x >> 8);
    default:
        return 0;
    }
}
