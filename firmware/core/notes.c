#include "notes.h"

void settings_default(hub_settings_t *s)
{
    s->base = 48;               /* C3 at the south-west key */
    s->step_a = 4;              /* the harmonic table */
    s->step_b = 7;
    s->channel = 0;
    s->aftertouch = 0;
    s->vel1 = 100;              /* one full travel a second: as soft as it gets */
    s->vel127 = 12000;          /* full travel in 8 ms */
    s->bend = 0;
    s->pd_volts = 15;
}

bool settings_ok(const hub_settings_t *s)
{
    return s->base >= -500 && s->base <= 500 &&
           s->step_a >= -24 && s->step_a <= 24 &&
           s->step_b >= -24 && s->step_b <= 24 &&
           s->channel <= 15 && s->aftertouch <= 1 &&
           s->vel1 >= 1 && s->vel127 > s->vel1 &&
           s->bend >= -100 && s->bend <= 100 &&
           (s->pd_volts == 5 || s->pd_volts == 9 || s->pd_volts == 12 || s->pd_volts == 15);
}

void notes_lattice(int u, int v, uint8_t key, int *a, int *b)
{
    int c = key / KEYS_ROWS, r = key % KEYS_ROWS;
    int ga = KEYS_COLS * u + c;
    int half = ga >= 0 ? ga / 2 : -((1 - ga) / 2);      /* floor(ga / 2) */

    *a = ga;
    *b = KEYS_ROWS * v + r - half;
}

int notes_note(const hub_settings_t *s, int u, int v, uint8_t key)
{
    int a, b;

    notes_lattice(u, v, key, &a, &b);
    int n = s->base + a * s->step_a + b * s->step_b;
    return n >= 0 && n <= 127 ? n : -1;
}

uint32_t notes_log2(uint32_t x)
{
    uint32_t n = 0;

    if (x == 0)
        return 0;
    while (n < 31 && (x >> (n + 1)) != 0)
        n++;                            /* the whole part: where the top bit is */

    /* The fraction, a bit at a time: square the number (kept between 1 and
     * 2); if that passes 2, the next bit is 1 and the number is halved. */
    uint32_t y = x << (31 - n);         /* 1.0 is 1 << 31 */
    uint32_t r = n << 16;
    for (int i = 15; i >= 0; i--) {
        uint64_t sq = ((uint64_t)y * y) >> 31;
        if (sq >> 32) {
            r |= 1u << i;
            sq >>= 1;
        }
        y = (uint32_t)sq;
    }
    return r;
}

/* Bend a fraction t (0..65536) of the way: Schlick's bias curve, which is a
 * straight line for bend 0 and lifts the middle for a positive bend. */
static uint8_t bent(int32_t t, int bend)
{
    int64_t b = 500 + 4 * bend;         /* thousandths: 100..900 */
    int64_t den = (1000 - 2 * b) * (65536 - t) + b * 65536;

    return (uint8_t)(1 + (126 * (int64_t)t * b + den / 2) / den);
}

uint8_t notes_velocity(const hub_settings_t *s, uint16_t speed)
{
    uint32_t l1 = notes_log2(s->vel1), l127 = notes_log2(s->vel127);
    uint32_t ls = notes_log2(speed ? speed : 1u);

    if (l127 <= l1)
        return 64;                      /* settings_ok() does not let this happen */
    if (ls <= l1)
        return 1;
    if (ls >= l127)
        return 127;
    return bent((int32_t)(((uint64_t)(ls - l1) << 16) / (l127 - l1)), s->bend);
}

uint8_t notes_velocity_mid(const hub_settings_t *s)
{
    return bent(32768, s->bend);
}
