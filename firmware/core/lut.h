/* The table that undoes the magnet's curve (tools/gen_lut.py makes it). */
#ifndef PIANO_LUT_H
#define PIANO_LUT_H

#include <stdint.h>

#define LUT_N 65

/* Sensor swing, 0 (key up) .. 65535 (key down), to travel on the same scale. */
extern const uint16_t lut_travel[LUT_N];

/* The other way: travel to sensor swing. The firmware does not need it; the
 * simulator uses it to make the readings a moving key would give. */
extern const uint16_t lut_field[LUT_N];

/* Look `in` up in a table, in a straight line between two entries. */
static inline uint16_t lut_apply(const uint16_t table[LUT_N], uint16_t in)
{
    uint32_t i = in >> 10, frac = in & 1023u;
    uint32_t a = table[i], b = table[i + 1];
    return (uint16_t)(a + (b - a) * frac / 1024u);
}

#endif
