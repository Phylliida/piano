/* I2C1 as a slow master, for the hub's CH224A (PB6 = SCL, PB7 = SDA).
 *
 * The lines have 1k in series and only the CH224A's own pull-ups, so the
 * clock is 10 kHz, and the bus is only usable while the charger powers
 * that chip: the caller's job (ch224_poll's `powered`). */
#ifndef PIANO_I2C_H
#define PIANO_I2C_H

#include "port.h"

/* `wait` (may be NULL) is called over and over while a transfer waits for
 * the bus: a register read takes 4 ms, and the cable and USB should not
 * stand still that long. */
void i2c1_start(void (*wait)(void));

/* Write `wn` bytes to the 7-bit address, then (if rn) a repeated start and
 * read `rn` bytes. True if every byte was acknowledged. The signature is
 * ch224_xfer_fn's; ctx is not used. */
bool i2c1_xfer(void *ctx, uint8_t addr, const uint8_t *w, size_t wn, uint8_t *r, size_t rn);

#endif
