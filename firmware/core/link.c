#include "link.h"

static void forget_neighbour(link_t *l)
{
    l->nbr_id = ID_NONE;
    l->nbr_kind = 0;
    l->nbr_port = 0;
    l->nbr_can = RATE_BASE;
}

static uint16_t draw_dwell(rng_t *rng)
{
    return (uint16_t)rng_range(rng, DWELL_MIN_MS, DWELL_MAX_MS);
}

/* --------------------------------------------------------------- the rate --- */

/* What this port offers its neighbour: nothing faster, once enough tries
 * with it have failed. */
static uint8_t offer(const link_t *l)
{
    return l->rate_fails < RATE_TRIES ? l->rate_max : RATE_BASE;
}

static void rate_enter(link_t *l, uint8_t state, uint32_t now)
{
    l->rate_state = state;
    l->rate_since = now;
}

/* At the base rate, with nothing under way. */
static void rate_base(link_t *l, uint32_t now)
{
    l->rate = RATE_BASE;
    l->ask_due = false;
    l->go_due = false;
    l->proven = false;
    rate_enter(l, RS_BASE, now);
}

/* A try at a faster rate came to nothing. */
static void rate_failed(link_t *l, uint32_t now)
{
    rate_base(l, now);
    if (l->rate_fails < 0xFFu)
        l->rate_fails++;
    l->retry_wait = true;
}

/* At the base rate, and the other end may not be: it went to the faster
 * rate and comes back a little earlier or later than we do, or its GO was
 * lost and it waits there for our call. Until it says that it is here,
 * nothing but HELLOs goes out: it would not hear it. */
static void rate_back(link_t *l, uint32_t now)
{
    rate_base(l, now);
    rate_enter(l, RS_BACK, now);
    l->retry_wait = true;
    l->hello_due = true;
    l->next_hello = now + RATE_CALL_MS;
}

uint8_t link_slow(const link_t *l)
{
    if (l->nbr_id == ID_NONE || l->rate >= l->rate_max)
        return SLOW_NOT;
    if (l->rate_fails >= RATE_TRIES)
        return SLOW_GAVE_UP;
    if (l->rate_fails)
        return SLOW_RETRY;
    return l->nbr_can <= l->rate ? SLOW_NBR : SLOW_NOT;
}

/* What a HELLO says about the rate. */
static void rate_hello(link_t *l, uint8_t flags, uint32_t now)
{
    uint8_t can = HELLO_CAN(flags);

    if (can >= RATE_COUNT)
        can = RATE_COUNT - 1;       /* a rate from the future: the fastest known here */

    /* A HELLO that was sent at another rate than the port's waited in the
     * UART while the rate changed. What it says is out of date. */
    if (HELLO_NOW(flags) != l->rate)
        return;

    if (l->rate_state == RS_BACK) {
        /* The other end is back as well, and shall know at once that we are. */
        rate_enter(l, RS_BASE, now);
        l->hello_due = true;
        l->next_hello = now + HELLO_PERIOD_MS;
    } else if ((flags & HELLO_BACK) && l->rate == RATE_BASE) {
        l->hello_due = true;        /* it has not heard us here yet */
    }

    if (flags & HELLO_GO) {
        /* The other end listens at `can` from now on and nowhere else: go
         * there too, give it time to get there, and call. */
        if (l->rate == RATE_BASE && can > RATE_BASE && can <= l->rate_max) {
            l->rate = can;
            l->ask_due = false;
            l->go_due = false;
            l->hello_due = false;
            l->next_hello = now + RATE_GUARD_MS;
            rate_enter(l, RS_CALL, now);
        }
        return;
    }
    l->nbr_can = can;

    if (l->rate == RATE_BASE) {
        /* "Let us go faster." The asker holds its frames back for the GO,
         * but not for long: if this program stood still meanwhile (it wrote
         * flash), the asking may be old, and a GO now would lose the frames
         * the asker sends again. No answer: it asks again later. */
        if ((flags & HELLO_ASK) && l->rate_state == RS_BASE && !l->go_due && !l->stalled &&
            since(now, l->ticked) < RATE_FRESH_MS) {
            uint8_t to = offer(l) < can ? offer(l) : can;

            if (to > RATE_BASE) {
                l->go_rate = to;
                l->go_due = true;
                l->ask_due = false;
                l->hello_due = true;
            }
        }
        return;
    }

    switch (l->rate_state) {
    case RS_CALL:
        /* The other end kept silent until it heard a call: so it hears us,
         * and we hear it. The HELLO that goes out now tells it. */
        rate_enter(l, RS_FAST, now);
        l->hello_due = true;
        l->next_hello = now + HELLO_PERIOD_MS;
        break;
    case RS_WAIT:
    case RS_ANSWER:
        if (flags & HELLO_ASK) {
            /* It calls: it has not heard us here yet. */
            l->rate_state = RS_ANSWER;
            l->hello_due = true;
            l->next_hello = now + RATE_CALL_MS;
        } else {
            rate_enter(l, RS_FAST, now);
            l->next_hello = now + HELLO_PERIOD_MS;
        }
        break;
    case RS_FAST:
        if (flags & HELLO_ASK)
            l->hello_due = true;
        break;
    default:
        break;
    }
}

static void rate_tick(link_t *l, uint32_t now)
{
    switch (l->rate_state) {
    case RS_BASE:
        if (l->retry_wait && since(now, l->rate_since) >= RATE_RETRY_MS)
            l->retry_wait = false;
        /* The end with the lower ID asks, once it knows that both can. */
        if (l->up && l->lead && l->nbr_id != ID_NONE && !l->retry_wait && !l->go_due &&
            !l->ask_due && offer(l) > RATE_BASE && l->nbr_can > RATE_BASE) {
            l->ask_due = true;
            l->hello_due = true;
            l->next_hello = now + HELLO_PERIOD_MS;  /* and no other HELLO behind it */
        }
        break;
    case RS_ASKED:
        /* No GO: the other end was busy, or the asking or the GO was lost.
         * That says nothing about the cable, so it is no try that failed;
         * but after a lost GO the other end is at the faster rate now. */
        if (since(now, l->rate_since) >= RATE_HOLD_MS)
            rate_back(l, now);
        break;
    case RS_CALL:
    case RS_WAIT:
    case RS_ANSWER:
        if (since(now, l->rate_since) >= RATE_TRY_MS) {
            rate_failed(l, now);
            rate_back(l, now);
        }
        break;
    case RS_BACK:
        if (since(now, l->rate_since) >= RATE_BACK_MS)
            rate_enter(l, RS_BASE, now);    /* it says nothing: no reason to wait for it */
        break;
    case RS_FAST:
        if (!l->proven && since(now, l->rate_since) >= RATE_PROVEN_MS) {
            l->proven = true;
            l->rate_fails = 0;
            l->retry_wait = false;
        }
        break;
    default:
        break;
    }
}

/* -------------------------------------------------------------------------- */

void link_restart(link_t *l, rng_t *rng, uint32_t now)
{
    l->up = false;
    forget_neighbour(l);
    rate_base(l, now);
    l->dwell = draw_dwell(rng);
    l->last_valid = now;
    l->listen_from = now;
    l->ticked = now;
    l->stalled = false;
    l->hello_due = true;
    l->next_hello = now + HELLO_PERIOD_MS;
}

void link_init(link_t *l, rng_t *rng, uint32_t now)
{
    l->swapped = false;
    l->rate_max = RATE_MAX;
    l->go_rate = RATE_BASE;
    l->rate_fails = 0;
    l->rate_nbr = ID_NONE;
    l->lead = false;
    l->retry_wait = false;
    link_restart(l, rng, now);
}

void link_heard(link_t *l, uint32_t now)
{
    l->up = true;
    l->last_valid = now;
    l->listen_from = now;
}

unsigned link_hello(link_t *l, const uint8_t *payload, uint8_t len, uint32_t own_id, uint32_t now)
{
    unsigned ev = 0;

    if (len < HELLO_MIN_LEN || payload[0] != PROTO_VERSION)
        return 0;

    uint8_t kind = payload[1];
    uint32_t id = get32(payload + 2);
    uint8_t port = payload[6];
    if (id == ID_NONE || id == ID_ALL || id == own_id)
        return 0;

    if (id != l->nbr_id || kind != l->nbr_kind || port != l->nbr_port) {
        l->nbr_id = id;
        l->nbr_kind = kind;
        l->nbr_port = port;
        ev = LINK_NBR;
    }
    l->lead = own_id < id;
    if (id != l->rate_nbr) {
        /* somebody else: what failed with the last one says nothing here */
        l->rate_nbr = id;
        l->rate_fails = 0;
        l->retry_wait = false;
    }
    /* a HELLO from before the two rates ends here: it offers the base rate */
    rate_hello(l, len >= HELLO_LEN ? payload[7] : 0, now);
    return ev;
}

unsigned link_tick(link_t *l, rng_t *rng, uint32_t now)
{
    unsigned ev = 0;

    /* What comes in until the next tick may have waited as long as we did. */
    l->stalled = since(now, l->ticked) >= RATE_FRESH_MS;
    l->ticked = now;
    if (l->up && since(now, l->last_valid) >= LINK_UP_MS) {
        l->up = false;
        ev |= LINK_WENT_DOWN;
        if (l->nbr_id != ID_NONE) {
            forget_neighbour(l);
            ev |= LINK_NBR;
        }
        if (l->rate != RATE_BASE) {
            /* Whoever is there now speaks at the base rate: listen there for
             * a whole dwell before turning the pins round. A faster rate
             * that lasted this short may be why the link went. */
            if (l->proven)
                rate_base(l, now);
            else
                rate_failed(l, now);
            l->listen_from = now;
            l->dwell = draw_dwell(rng);
            l->hello_due = true;
            l->next_hello = now + HELLO_PERIOD_MS;
        } else {
            rate_base(l, now);
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

    rate_tick(l, now);

    /* A port that waits for the other end's call says nothing at all. */
    if (l->rate_state != RS_WAIT && reached(now, l->next_hello)) {
        bool calling = l->rate_state == RS_CALL || l->rate_state == RS_ANSWER ||
                       l->rate_state == RS_BACK;

        l->hello_due = true;
        l->next_hello = now + (calling ? RATE_CALL_MS : HELLO_PERIOD_MS);
    }
    return ev;
}

void link_hello_payload(uint8_t *payload, uint8_t kind, uint32_t id, uint8_t port)
{
    payload[0] = PROTO_VERSION;
    payload[1] = kind;
    put32(payload + 2, id);
    payload[6] = port;
    payload[7] = 0;
}

void link_hello_out(link_t *l, uint8_t *payload, uint8_t kind, uint32_t id, uint8_t port,
                    uint32_t now)
{
    uint8_t flags = (uint8_t)(l->rate << 2);

    link_hello_payload(payload, kind, id, port);
    l->hello_due = false;
    if (l->go_due) {
        /* The last frame at this rate. Behind it this port keeps silent at
         * the new rate until the other end calls. */
        flags |= HELLO_GO | l->go_rate;
        l->go_due = false;
        l->rate = l->go_rate;
        rate_enter(l, RS_WAIT, now);
    } else {
        flags |= offer(l);
        if (l->ask_due) {
            flags |= HELLO_ASK;
            l->ask_due = false;
            rate_enter(l, RS_ASKED, now);
        } else if (l->rate_state == RS_CALL || l->rate_state == RS_ANSWER) {
            flags |= HELLO_ASK;
        } else if (l->rate_state == RS_BACK) {
            flags |= HELLO_BACK;
        }
    }
    payload[7] = flags;
}
