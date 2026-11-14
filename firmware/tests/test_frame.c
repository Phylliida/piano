/* util.c and frame.c: checksums, COBS, frames, the receiver. */
#include "frame.h"
#include "t.h"
#include "util.h"

static void checksums(void)
{
    const uint8_t *s = (const uint8_t *)"123456789";

    CHECK_EQ(crc16_ccitt(s, 9), 0x29B1);        /* the published check values */
    CHECK_EQ(crc32_ieee(s, 9), 0xCBF43926u);
    CHECK_EQ(crc16_ccitt(s, 0), 0xFFFF);
}

static void ids(void)
{
    uint8_t uid[12] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12};
    uint32_t a = id_from_uid(uid);

    uid[11] ^= 1;
    CHECK(a != id_from_uid(uid));
    CHECK(a != ID_NONE && a != ID_ALL);
    /* FNV-1a of twelve zero bytes, worked out by hand from the definition */
    memset(uid, 0, sizeof uid);
    uint32_t h = 2166136261u;
    for (int i = 0; i < 12; i++)
        h *= 16777619u;
    CHECK_EQ(id_from_uid(uid), h);
}

static void byte_order_and_time(void)
{
    uint8_t b[4];

    put16(b, 0x1234);
    CHECK_EQ(b[0], 0x34);
    CHECK_EQ(get16(b), 0x1234);
    put32(b, 0xA1B2C3D4u);
    CHECK_EQ(b[0], 0xD4);
    CHECK_EQ(b[3], 0xA1);
    CHECK_EQ(get32(b), 0xA1B2C3D4u);

    /* times wrap */
    CHECK_EQ(since(5, 0xFFFFFFFBu), 10);
    CHECK(reached(5, 0xFFFFFFFBu));
    CHECK(!reached(0xFFFFFFFBu, 5));
    CHECK(reached(7, 7));
}

static void random_numbers(void)
{
    rng_t r;
    bool lo = false, hi = false;

    rng_seed(&r, 0);                            /* 0 would stick at 0 */
    CHECK(rng_next(&r) != 0);
    for (int i = 0; i < 20000; i++) {
        uint32_t v = rng_range(&r, 250, 500);
        CHECK(v >= 250 && v <= 500);
        lo |= v == 250;
        hi |= v == 500;
    }
    CHECK(lo && hi);
}

static void cobs_known(void)
{
    uint8_t out[300], back[300];

    const uint8_t a[] = {0};
    CHECK_EQ(cobs_encode(a, 1, out), 2);
    CHECK(out[0] == 1 && out[1] == 1);

    const uint8_t b[] = {0x11, 0x22, 0x00, 0x33};
    const uint8_t b_enc[] = {3, 0x11, 0x22, 2, 0x33};
    CHECK_EQ(cobs_encode(b, 4, out), 5);
    CHECK(memcmp(out, b_enc, 5) == 0);

    CHECK_EQ(cobs_encode(b, 0, out), 1);        /* nothing: one length byte */
    CHECK_EQ(out[0], 1);

    /* 254 non-zero bytes fill a block exactly */
    uint8_t big[254];
    for (int i = 0; i < 254; i++)
        big[i] = (uint8_t)(i + 1);
    size_t n = cobs_encode(big, 254, out);
    CHECK_EQ(n, 256);
    CHECK_EQ(out[0], 0xFF);
    CHECK_EQ(out[255], 1);
    CHECK_EQ(cobs_decode(out, n, back, sizeof back), 254);
    CHECK(memcmp(back, big, 254) == 0);
}

static void cobs_round_trip(void)
{
    rng_t r;
    uint8_t in[70], enc[80], back[70];

    rng_seed(&r, 7);
    for (int trial = 0; trial < 3000; trial++) {
        size_t n = rng_range(&r, 1, 66);
        for (size_t i = 0; i < n; i++)
            in[i] = (rng_next(&r) & 3) ? (uint8_t)rng_next(&r) : 0;   /* plenty of zeros */
        size_t e = cobs_encode(in, n, enc);
        CHECK(e == n + 1);
        for (size_t i = 0; i < e; i++)
            CHECK(enc[i] != 0);
        CHECK_EQ(cobs_decode(enc, e, back, sizeof back), n);
        CHECK(memcmp(in, back, n) == 0);
    }
}

static void cobs_bad_input(void)
{
    uint8_t out[8];

    const uint8_t zero_inside[] = {3, 0x11, 0x00, 2, 0x33};
    CHECK_EQ(cobs_decode(zero_inside, 5, out, sizeof out), 0);
    const uint8_t cut_short[] = {5, 0x11, 0x22};
    CHECK_EQ(cobs_decode(cut_short, 3, out, sizeof out), 0);
    const uint8_t zero_code[] = {0, 0x11};
    CHECK_EQ(cobs_decode(zero_code, 2, out, sizeof out), 0);
    const uint8_t fits[] = {3, 0x11, 0x22, 2, 0x33};
    CHECK_EQ(cobs_decode(fits, 5, out, 4), 4);
    CHECK_EQ(cobs_decode(fits, 5, out, 3), 0);  /* no room */
}

/* Feed wire bytes to a receiver; returns how many good frames came out and
 * leaves the last one in type/payload/len. */
static int feed(frame_rx_t *rx, const uint8_t *wire, size_t n, int *bad,
                uint8_t *type, uint8_t *payload, uint8_t *len)
{
    int good = 0;

    for (size_t i = 0; i < n; i++) {
        frame_result_t res = frame_rx_byte(rx, wire[i], type, payload, len);
        if (res == FRAME_OK)
            good++;
        else if (res == FRAME_BAD && bad)
            (*bad)++;
    }
    return good;
}

static void frame_round_trip(void)
{
    rng_t r;
    frame_rx_t rx;
    uint8_t pl[FRAME_MAX_PAYLOAD + 1], wire[FRAME_MAX_WIRE], got[FRAME_MAX_PAYLOAD];
    uint8_t type, len;

    rng_seed(&r, 99);
    frame_rx_reset(&rx);
    for (size_t n = 0; n <= FRAME_MAX_PAYLOAD; n++) {
        for (int trial = 0; trial < 20; trial++) {
            for (size_t i = 0; i < n; i++)
                pl[i] = (rng_next(&r) & 1) ? (uint8_t)rng_next(&r) : 0;
            uint8_t t = (uint8_t)rng_next(&r);
            size_t w = frame_encode(t, pl, n, wire);
            CHECK(w >= 5 && w <= FRAME_MAX_WIRE);
            CHECK_EQ(w, n + 5);                 /* type, crc (2), COBS (1), 0x00 */
            CHECK_EQ(wire[w - 1], 0);
            for (size_t i = 0; i + 1 < w; i++)
                CHECK(wire[i] != 0);
            CHECK_EQ(feed(&rx, wire, w, NULL, &type, got, &len), 1);
            CHECK_EQ(type, t);
            CHECK_EQ(len, n);
            CHECK(memcmp(got, pl, n) == 0);
        }
    }
    CHECK_EQ(frame_encode(1, pl, FRAME_MAX_PAYLOAD + 1, wire), 0);
}

static void frame_damage(void)
{
    frame_rx_t rx;
    uint8_t pl[40], wire[FRAME_MAX_WIRE], copy[FRAME_MAX_WIRE], got[FRAME_MAX_PAYLOAD];
    uint8_t type, len;
    int good = 0, bad = 0;

    for (int i = 0; i < 40; i++)
        pl[i] = (uint8_t)(i * 7);
    size_t w = frame_encode(FT_NOTE, pl, 40, wire);

    /* every single wrong bit: never a good frame */
    for (size_t bit = 0; bit < (w - 1) * 8; bit++) {
        memcpy(copy, wire, w);
        copy[bit / 8] ^= (uint8_t)(1u << (bit % 8));
        frame_rx_reset(&rx);
        good += feed(&rx, copy, w, &bad, &type, got, &len);
    }
    CHECK_EQ(good, 0);
    CHECK(bad >= (int)((w - 1) * 8) - 16);      /* a byte turned 0x00 may leave two short halves */

    /* a byte missing, a byte doubled */
    for (size_t at = 0; at + 1 < w; at++) {
        frame_rx_reset(&rx);
        memcpy(copy, wire, at);
        memcpy(copy + at, wire + at + 1, w - at - 1);
        CHECK_EQ(feed(&rx, copy, w - 1, NULL, &type, got, &len), 0);
    }
}

static void receiver(void)
{
    frame_rx_t rx;
    uint8_t wire[3 * FRAME_MAX_WIRE + 200], got[FRAME_MAX_PAYLOAD];
    uint8_t type, len;
    int bad = 0;
    size_t n = 0;

    frame_rx_reset(&rx);

    /* zeros alone are nothing, not errors */
    memset(wire, 0, 10);
    CHECK_EQ(feed(&rx, wire, 10, &bad, &type, got, &len), 0);
    CHECK_EQ(bad, 0);

    /* the tail of somebody's frame, then two good ones back to back */
    wire[n++] = 0x55;
    wire[n++] = 0x66;
    wire[n++] = 0;
    n += frame_encode(FT_HELLO, (const uint8_t *)"abc", 3, wire + n);
    n += frame_encode(FT_BEACON, (const uint8_t *)"defgh", 5, wire + n);
    CHECK_EQ(feed(&rx, wire, n, &bad, &type, got, &len), 2);
    CHECK_EQ(bad, 1);
    CHECK_EQ(type, FT_BEACON);
    CHECK_EQ(len, 5);
    CHECK(memcmp(got, "defgh", 5) == 0);

    /* far too long, then a good one: the long one is one error and no more */
    bad = 0;
    n = 0;
    memset(wire, 0x41, 200);
    n = 200;
    wire[n++] = 0;
    n += frame_encode(FT_INFO, (const uint8_t *)"x", 1, wire + n);
    CHECK_EQ(feed(&rx, wire, n, &bad, &type, got, &len), 1);
    CHECK_EQ(bad, 1);
    CHECK_EQ(type, FT_INFO);

    /* a frame with no payload */
    n = frame_encode(0x7E, NULL, 0, wire);
    CHECK_EQ(feed(&rx, wire, n, &bad, &type, got, &len), 1);
    CHECK_EQ(type, 0x7E);
    CHECK_EQ(len, 0);

    /* reset throws away what was collected */
    n = frame_encode(FT_INFO, (const uint8_t *)"hello", 5, wire);
    feed(&rx, wire, 4, &bad, &type, got, &len);
    frame_rx_reset(&rx);
    CHECK_EQ(feed(&rx, wire, n, &bad, &type, got, &len), 1);
    CHECK_EQ(len, 5);
}

/* The small printf, against the C library's. */
static void text(void)
{
    char a[64], b[64];

#define SAME(...) do { \
        size_t n = fmt(a, sizeof a, __VA_ARGS__); \
        snprintf(b, sizeof b, __VA_ARGS__); \
        CHECK(strcmp(a, b) == 0); \
        CHECK_EQ(n, strlen(b)); \
    } while (0)

    SAME("plain");
    SAME("%d %d %d", 0, 42, -42);
    SAME("%d %d", 2147483647, -2147483647 - 1);
    SAME("%u %u", 0u, 4294967295u);
    SAME("%x %08x %04x", 0xbeefu, 0x1a2b3cu, 0xfu);
    SAME("%5d|%05d|%2d|%05d", 42, 42, 12345, -42);
    SAME("%3u|%03u", 7u, 7u);
    SAME("%s and %6s|", "this", "that");
    SAME("%c%c 100%%", 'o', 'k');
    SAME("tile %d %08x at %d,%d", 3, 0xA1B2C3D4u, -1, 2);
#undef SAME

    /* too long: cut, and still ends in a 0 */
    CHECK_EQ(fmt(a, 6, "%s", "0123456789"), 5);
    CHECK(strcmp(a, "01234") == 0);
    CHECK_EQ(fmt(a, 4, "%d", 123456), 3);
    CHECK(strcmp(a, "123") == 0);
    CHECK_EQ(fmt(a, 1, "abc"), 0);
    CHECK_EQ(a[0], 0);
    a[0] = 'x';
    CHECK_EQ(fmt(a, 0, "abc"), 0);
    CHECK_EQ(a[0], 'x');                        /* no room at all: nothing written */
}

int main(void)
{
    RUN(text);
    RUN(checksums);
    RUN(ids);
    RUN(byte_order_and_time);
    RUN(random_numbers);
    RUN(cobs_known);
    RUN(cobs_round_trip);
    RUN(cobs_bad_input);
    RUN(frame_round_trip);
    RUN(frame_damage);
    RUN(receiver);
    return T_DONE();
}
