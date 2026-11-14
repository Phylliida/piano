/* From a key on a tile to a MIDI note, and from a speed to a velocity:
 * DESIGN.md, "Notes". Pure arithmetic on the hub's settings. */
#ifndef PIANO_NOTES_H
#define PIANO_NOTES_H

#include "store.h"

void settings_default(hub_settings_t *s);

/* Are all the values ones the hub can work with? */
bool settings_ok(const hub_settings_t *s);

/* The lattice point of key `key` (5 * column + row) on the tile at (u, v):
 * a = 8u + c, b = 5v + r - floor(a / 2). */
void notes_lattice(int u, int v, uint8_t key, int *a, int *b);

/* Its MIDI note, or -1 if that is outside 0..127. */
int notes_note(const hub_settings_t *s, int u, int v, uint8_t key);

/* MIDI velocity, 1..127, for a speed in hundredths of full travel per
 * second: it follows the logarithm of the speed from `vel1` to `vel127`,
 * bent by `bend`. */
uint8_t notes_velocity(const hub_settings_t *s, uint16_t speed);

/* The velocity half way along that curve, for a note whose speed nobody
 * measured. */
uint8_t notes_velocity_mid(const hub_settings_t *s);

/* log2(x) for x >= 1, as a 16.16 fixed-point number. Here for the tests. */
uint32_t notes_log2(uint32_t x);

#endif
