/* Frames on a cable: COBS(type, payload, crc16) followed by one 0x00. */
#ifndef PIANO_FRAME_H
#define PIANO_FRAME_H

#include "proto.h"

/* COBS: rewrite n bytes so that none is 0x00. Writes n + 1 + n/254 bytes at
 * most and returns how many. */
size_t cobs_encode(const uint8_t *in, size_t n, uint8_t *out);

/* Undo it. Returns the decoded length, or 0 if the input is not valid COBS
 * or does not fit in cap bytes. */
size_t cobs_decode(const uint8_t *in, size_t n, uint8_t *out, size_t cap);

/* The bytes of one frame as they go on the wire, the closing 0x00 included.
 * wire must hold FRAME_MAX_WIRE bytes. Returns the count, or 0 if the payload
 * is longer than FRAME_MAX_PAYLOAD. */
size_t frame_encode(uint8_t type, const uint8_t *payload, size_t len, uint8_t *wire);

/* The receiving side of one port: collects bytes up to the next 0x00. */
typedef struct {
    uint8_t buf[FRAME_MAX_WIRE];
    uint8_t len;
    bool too_long;
} frame_rx_t;

typedef enum {
    FRAME_NONE,     /* still collecting */
    FRAME_OK,       /* a good frame: *type, payload and *len are set */
    FRAME_BAD,      /* a frame ended and failed COBS, length or CRC */
} frame_result_t;

void frame_rx_reset(frame_rx_t *rx);

/* Take one received byte. payload must hold FRAME_MAX_PAYLOAD bytes. */
frame_result_t frame_rx_byte(frame_rx_t *rx, uint8_t byte,
                             uint8_t *type, uint8_t *payload, uint8_t *len);

#endif
