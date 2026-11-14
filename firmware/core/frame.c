#include "frame.h"

#include <string.h>

#include "util.h"

size_t cobs_encode(const uint8_t *in, size_t n, uint8_t *out)
{
    size_t w = 1;       /* next byte to write */
    size_t code_at = 0; /* where the current block's length byte goes */
    uint8_t code = 1;   /* that length: 1 + the non-zero bytes after it */

    for (size_t i = 0; i < n; i++) {
        if (in[i] == 0) {
            out[code_at] = code;
            code_at = w++;
            code = 1;
        } else {
            out[w++] = in[i];
            code++;
            if (code == 0xFF) {     /* a full block of 254: no zero is implied after it */
                out[code_at] = code;
                code_at = w++;
                code = 1;
            }
        }
    }
    out[code_at] = code;
    return w;
}

size_t cobs_decode(const uint8_t *in, size_t n, uint8_t *out, size_t cap)
{
    size_t r = 0, w = 0;

    while (r < n) {
        uint8_t code = in[r++];
        if (code == 0)
            return 0;
        size_t run = (size_t)code - 1u;
        if (r + run > n || w + run > cap)
            return 0;
        for (size_t i = 0; i < run; i++) {
            if (in[r] == 0)
                return 0;
            out[w++] = in[r++];
        }
        /* every block but a full one, and but the last, stands for a zero */
        if (code != 0xFF && r < n) {
            if (w >= cap)
                return 0;
            out[w++] = 0;
        }
    }
    return w;
}

size_t frame_encode(uint8_t type, const uint8_t *payload, size_t len, uint8_t *wire)
{
    uint8_t body[FRAME_MAX_BODY];

    if (len > FRAME_MAX_PAYLOAD)
        return 0;
    body[0] = type;
    if (len)
        memcpy(body + 1, payload, len);
    put16(body + 1 + len, crc16_ccitt(body, 1 + len));

    size_t n = cobs_encode(body, 1 + len + 2, wire);
    wire[n++] = 0;
    return n;
}

void frame_rx_reset(frame_rx_t *rx)
{
    rx->len = 0;
    rx->too_long = false;
}

frame_result_t frame_rx_byte(frame_rx_t *rx, uint8_t byte,
                             uint8_t *type, uint8_t *payload, uint8_t *len)
{
    uint8_t body[FRAME_MAX_BODY];

    if (byte != 0) {
        if (rx->len < sizeof rx->buf)
            rx->buf[rx->len++] = byte;
        else
            rx->too_long = true;
        return FRAME_NONE;
    }

    /* 0x00: the frame ends here */
    size_t n = rx->len;
    bool too_long = rx->too_long;
    frame_rx_reset(rx);
    if (n == 0 && !too_long)
        return FRAME_NONE;          /* two zeros in a row: nothing between them */
    if (too_long)
        return FRAME_BAD;

    size_t blen = cobs_decode(rx->buf, n, body, sizeof body);
    if (blen < 3)
        return FRAME_BAD;
    if (get16(body + blen - 2) != crc16_ccitt(body, blen - 2))
        return FRAME_BAD;

    *type = body[0];
    *len = (uint8_t)(blen - 3);
    memcpy(payload, body + 1, blen - 3);
    return FRAME_OK;
}
