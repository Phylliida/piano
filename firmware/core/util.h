/* Small helpers every core file uses: byte order, time, IDs, random numbers. */
#ifndef PIANO_UTIL_H
#define PIANO_UTIL_H

#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* ---- little-endian fields ---- */
static inline void put16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
}

static inline void put32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static inline uint16_t get16(const uint8_t *p)
{
    return (uint16_t)(p[0] | ((uint16_t)p[1] << 8));
}

static inline uint32_t get32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* ---- time ----
 * Times are milliseconds in a uint32_t that wraps after 49 days, so they are
 * only ever compared through their difference. */
static inline uint32_t since(uint32_t now, uint32_t then)
{
    return now - then;
}

static inline bool reached(uint32_t now, uint32_t when)
{
    return (int32_t)(now - when) >= 0;
}

/* ---- IDs ---- */

/* A node's ID: FNV-1a of the chip's 96-bit unique ID. 0 and 0xFFFFFFFF mean
 * "nobody" and "everybody", so a hash that lands there is moved off. */
uint32_t id_from_uid(const uint8_t uid[12]);

/* ---- random numbers: xorshift32, good enough to spread timers ---- */
typedef struct {
    uint32_t s;
} rng_t;

void rng_seed(rng_t *r, uint32_t seed);
uint32_t rng_next(rng_t *r);
/* A number from lo to hi, both included. */
uint32_t rng_range(rng_t *r, uint32_t lo, uint32_t hi);

/* ---- text ----
 * A small printf, so the firmware does not carry the C library's: %d %u %x
 * %s %c and %%, with an optional width, and a 0 in front of the width to pad
 * a number with zeros. Writes at most max - 1 characters and a 0; returns
 * how many characters it wrote. */
size_t vfmt(char *out, size_t max, const char *f, va_list ap);
size_t fmt(char *out, size_t max, const char *f, ...) __attribute__((format(printf, 3, 4)));

/* ---- checksums ---- */
uint16_t crc16_ccitt(const uint8_t *data, size_t n);     /* CRC-16/CCITT-FALSE */
uint32_t crc32_ieee(const uint8_t *data, size_t n);      /* the zlib one */

#endif
