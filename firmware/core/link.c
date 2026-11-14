#include "link.h"

static void forget_neighbour(link_t *l)
{
    l->nbr_id = ID_NONE;
    l->nbr_kind = 0;
    l->nbr_port = 0;
}

static uint16_t draw_dwell(rng_t *rng)
{
    return (uint16_t)rng_range(rng, DWELL_MIN_MS, DWELL_MAX_MS);
}

void link_restart(link_t *l, rng_t *rng, uint32_t now)
{
    l->up = false;
    forget_neighbour(l);
    l->dwell = draw_dwell(rng);
    l->last_valid = now;
    l->listen_from = now;
    l->hello_due = true;
    l->next_hello = now + HELLO_PERIOD_MS;
}

void link_init(link_t *l, rng_t *rng, uint32_t now)
{
    l->swapped = false;
    link_restart(l, rng, now);
}

void link_heard(link_t *l, uint32_t now)
{
    l->up = true;
    l->last_valid = now;
    l->listen_from = now;
}

unsigned link_hello(link_t *l, const uint8_t *payload, uint8_t len, uint32_t own_id)
{
    if (len < HELLO_LEN || payload[0] != PROTO_VERSION)
        return 0;

    uint8_t kind = payload[1];
    uint32_t id = get32(payload + 2);
    uint8_t port = payload[6];
    if (id == ID_NONE || id == ID_ALL || id == own_id)
        return 0;
    if (id == l->nbr_id && kind == l->nbr_kind && port == l->nbr_port)
        return 0;

    l->nbr_id = id;
    l->nbr_kind = kind;
    l->nbr_port = port;
    return LINK_NBR;
}

unsigned link_tick(link_t *l, rng_t *rng, uint32_t now)
{
    unsigned ev = 0;

    if (l->up && since(now, l->last_valid) >= LINK_UP_MS) {
        l->up = false;
        ev |= LINK_WENT_DOWN;
        if (l->nbr_id != ID_NONE) {
            forget_neighbour(l);
            ev |= LINK_NBR;
        }
    }

    /* Nothing heard for a whole dwell: try the pins the other way round, and
     * say hello at once so the other side hears us as early as it can. */
    if (!l->up && since(now, l->listen_from) >= l->dwell) {
        l->swapped = !l->swapped;
        l->listen_from = now;
        l->dwell = draw_dwell(rng);
        l->hello_due = true;
        l->next_hello = now + HELLO_PERIOD_MS;
        ev |= LINK_SWAPPED;
    }

    if (reached(now, l->next_hello)) {
        l->hello_due = true;
        l->next_hello = now + HELLO_PERIOD_MS;
    }
    return ev;
}

void link_hello_payload(uint8_t *payload, uint8_t kind, uint32_t id, uint8_t port)
{
    payload[0] = PROTO_VERSION;
    payload[1] = kind;
    put32(payload + 2, id);
    payload[6] = port;
}
