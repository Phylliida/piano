#include "store.h"

#include "util.h"

static void head(uint8_t *out, uint32_t magic)
{
    put32(out, magic);
    put16(out + 4, STORE_VERSION);
    put16(out + 6, 0);
}

/* Is this a whole record of the kind and length wanted? */
static bool whole(const uint8_t *in, size_t len, uint32_t magic, size_t want)
{
    if (in == NULL || len < want)
        return false;
    if (get32(in) != magic || get16(in + 4) != STORE_VERSION)
        return false;
    return get32(in + want - 4) == crc32_ieee(in, want - 4);
}

void store_tile_pack(const tile_record_t *r, uint8_t out[STORE_TILE_LEN])
{
    uint8_t *p = out + STORE_HEAD_LEN;

    head(out, STORE_TILE_MAGIC);
    for (unsigned i = 1; i <= TILE_PARAMS; i++, p += 4)
        put32(p, (uint32_t)r->param[i]);
    for (unsigned i = 0; i < KEYS_N; i++, p += 4) {
        put16(p, r->cal[i].rest);
        put16(p + 2, r->cal[i].bottom);
    }
    put32(p, crc32_ieee(out, STORE_TILE_LEN - 4));
}

bool store_tile_unpack(const uint8_t *in, size_t len, tile_record_t *r)
{
    const uint8_t *p = in + STORE_HEAD_LEN;

    if (!whole(in, len, STORE_TILE_MAGIC, STORE_TILE_LEN))
        return false;
    r->param[0] = 0;
    for (unsigned i = 1; i <= TILE_PARAMS; i++, p += 4)
        r->param[i] = (int32_t)get32(p);
    for (unsigned i = 0; i < KEYS_N; i++, p += 4) {
        r->cal[i].rest = get16(p);
        r->cal[i].bottom = get16(p + 2);
    }
    return true;
}

void store_hub_pack(const hub_settings_t *s, uint8_t out[STORE_HUB_LEN])
{
    uint8_t *p = out + STORE_HEAD_LEN;

    head(out, STORE_HUB_MAGIC);
    put16(p, (uint16_t)s->base);
    p[2] = (uint8_t)s->step_a;
    p[3] = (uint8_t)s->step_b;
    p[4] = s->channel;
    p[5] = s->aftertouch;
    put16(p + 6, s->vel1);
    put16(p + 8, s->vel127);
    p[10] = (uint8_t)s->bend;
    p[11] = s->pd_volts;
    put32(p + 12, crc32_ieee(out, STORE_HUB_LEN - 4));
}

bool store_hub_unpack(const uint8_t *in, size_t len, hub_settings_t *s)
{
    const uint8_t *p = in + STORE_HEAD_LEN;

    if (!whole(in, len, STORE_HUB_MAGIC, STORE_HUB_LEN))
        return false;
    s->base = (int16_t)get16(p);
    s->step_a = (int8_t)p[2];
    s->step_b = (int8_t)p[3];
    s->channel = p[4];
    s->aftertouch = p[5];
    s->vel1 = get16(p + 6);
    s->vel127 = get16(p + 8);
    s->bend = (int8_t)p[10];
    s->pd_volts = p[11];
    return true;
}
