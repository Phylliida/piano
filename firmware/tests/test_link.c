/* link.c: HELLO, up and down, finding which way round the cable is, and
 * agreeing on how fast. */
#include "link.h"
#include "t.h"

static void starts_straight(void)
{
    rng_t r;
    link_t l;

    rng_seed(&r, 1);
    link_init(&l, &r, 1000);
    CHECK(!l.swapped);
    CHECK(!l.up);
    CHECK(l.hello_due);                         /* says hello at once */
    CHECK(l.dwell >= DWELL_MIN_MS && l.dwell <= DWELL_MAX_MS);
    CHECK_EQ(l.nbr_id, ID_NONE);
}

static void hello_every_100ms(void)
{
    rng_t r;
    link_t l;
    int hellos = 0;

    rng_seed(&r, 2);
    link_init(&l, &r, 0);
    l.hello_due = false;
    for (uint32_t t = 1; t <= 1000; t++) {
        link_heard(&l, t);                      /* keep it up, so it never swaps */
        link_tick(&l, &r, t);
        if (l.hello_due) {
            hellos++;
            l.hello_due = false;
        }
    }
    CHECK_EQ(hellos, 10);
}

static void swaps_when_deaf(void)
{
    rng_t r;
    link_t l;
    uint32_t last = 0;
    int swaps = 0;
    bool varied = false;
    uint32_t first_gap = 0;

    rng_seed(&r, 3);
    link_init(&l, &r, 0);
    for (uint32_t t = 1; t <= 20000; t++) {
        l.hello_due = false;
        unsigned ev = link_tick(&l, &r, t);
        if (ev & LINK_SWAPPED) {
            uint32_t gap = t - last;
            CHECK(gap >= DWELL_MIN_MS && gap <= DWELL_MAX_MS);
            CHECK(l.hello_due);                 /* hello at once the new way round */
            CHECK_EQ(l.swapped, (swaps % 2) == 0);
            if (swaps == 0)
                first_gap = gap;
            else if (gap != first_gap)
                varied = true;
            last = t;
            swaps++;
        }
        CHECK(!(ev & LINK_WENT_DOWN));          /* it never was up */
    }
    CHECK(swaps >= 40 && swaps <= 80);
    CHECK(varied);
}

static void up_and_down(void)
{
    rng_t r;
    link_t l;
    uint8_t pl[HELLO_LEN];

    rng_seed(&r, 4);
    link_init(&l, &r, 0);

    link_heard(&l, 10);
    CHECK(l.up);
    link_hello_payload(pl, KIND_TILE, 0xABCD1234u, PORT_E);
    CHECK_EQ(link_hello(&l, pl, HELLO_LEN, 0x11111111u, 0), LINK_NBR);
    CHECK_EQ(l.nbr_id, 0xABCD1234u);
    CHECK_EQ(l.nbr_port, PORT_E);
    CHECK_EQ(l.nbr_kind, KIND_TILE);
    CHECK_EQ(link_hello(&l, pl, HELLO_LEN, 0x11111111u, 0), 0);    /* nothing new */

    /* stays up, and does not swap, while frames keep coming */
    for (uint32_t t = 11; t < 3000; t++) {
        if (t % 300 == 0)
            link_heard(&l, t);
        CHECK_EQ(link_tick(&l, &r, t) & (LINK_SWAPPED | LINK_WENT_DOWN), 0);
    }
    CHECK(l.up && !l.swapped);

    /* silence: down after LINK_UP_MS, the neighbour forgotten */
    uint32_t down_at = 0, swap_at = 0;
    for (uint32_t t = 3000; t < 4000; t++) {
        unsigned ev = link_tick(&l, &r, t);
        if (ev & LINK_WENT_DOWN) {
            CHECK(ev & LINK_NBR);
            down_at = t;
        }
        if ((ev & LINK_SWAPPED) && !swap_at)
            swap_at = t;
    }
    CHECK_EQ(down_at, 2700 + LINK_UP_MS);
    CHECK_EQ(l.nbr_id, ID_NONE);
    CHECK(swap_at >= down_at);                  /* an up port does not swap */
}

static void hello_rules(void)
{
    rng_t r;
    link_t l;
    uint8_t pl[HELLO_LEN];

    rng_seed(&r, 5);
    link_init(&l, &r, 0);

    link_hello_payload(pl, KIND_HUB, 0x22222222u, 0);
    CHECK_EQ(pl[0], PROTO_VERSION);
    CHECK_EQ(link_hello(&l, pl, HELLO_MIN_LEN - 1, 1, 0), 0);      /* too short */
    pl[0] = PROTO_VERSION + 1;
    CHECK_EQ(link_hello(&l, pl, HELLO_LEN, 1, 0), 0);              /* another version */
    pl[0] = PROTO_VERSION;
    CHECK_EQ(link_hello(&l, pl, HELLO_LEN, 0x22222222u, 0), 0);    /* ourselves */
    CHECK_EQ(l.nbr_id, ID_NONE);

    link_hello_payload(pl, KIND_TILE, ID_NONE, 0);
    CHECK_EQ(link_hello(&l, pl, HELLO_LEN, 1, 0), 0);
    link_hello_payload(pl, KIND_TILE, ID_ALL, 0);
    CHECK_EQ(link_hello(&l, pl, HELLO_LEN, 1, 0), 0);

    link_hello_payload(pl, KIND_HUB, 0x22222222u, 0);
    CHECK_EQ(link_hello(&l, pl, HELLO_LEN, 1, 0), LINK_NBR);
    CHECK_EQ(l.nbr_kind, KIND_HUB);
    /* the same node on another of its ports is a change */
    link_hello_payload(pl, KIND_HUB, 0x22222222u, 2);
    CHECK_EQ(link_hello(&l, pl, HELLO_LEN, 1, 0), LINK_NBR);
    CHECK_EQ(l.nbr_port, 2);
}

/* Two ports on one cable. A straight port sends on pin A; a cable either
 * crosses A and B or not. One side hears the other only when the sender's
 * sending pin lands on the listener's listening pin. Returns the time at
 * which both were up, or 0 if that never happened; checks that they then
 * stay up and stop swapping. */
static uint32_t run_pair(uint32_t seed_a, uint32_t seed_b, bool crossed, uint32_t start_b)
{
    rng_t ra, rb;
    link_t a, b;
    uint32_t both_up = 0;

    rng_seed(&ra, seed_a);
    rng_seed(&rb, seed_b);
    link_init(&a, &ra, 0);
    bool b_on = false;

    for (uint32_t t = 1; t <= 30000; t++) {
        if (!b_on && t >= start_b) {
            link_init(&b, &rb, t);
            b_on = true;
        }
        unsigned ea = link_tick(&a, &ra, t);
        unsigned eb = b_on ? link_tick(&b, &rb, t) : 0;
        bool heard = b_on && ((a.swapped != b.swapped) != crossed);

        if (a.hello_due) {
            a.hello_due = false;
            if (heard)
                link_heard(&b, t);
        }
        if (b_on && b.hello_due) {
            b.hello_due = false;
            if (heard)
                link_heard(&a, t);
        }
        if (!both_up && b_on && a.up && b.up)
            both_up = t;
        if (both_up && t > both_up) {
            CHECK(a.up && b.up);
            CHECK_EQ((ea | eb) & (LINK_SWAPPED | LINK_WENT_DOWN), 0);
            if (t > both_up + 3000)
                break;
        }
    }
    return both_up;
}

static void finds_the_cable(void)
{
    uint32_t worst = 0;

    for (uint32_t i = 1; i <= 300; i++) {
        for (int crossed = 0; crossed <= 1; crossed++) {
            /* B starts anywhere within A's first second */
            uint32_t start_b = 1 + (i * 37u) % 1000u;
            uint32_t up = run_pair(i * 2654435761u, i * 40503u + 17u, crossed, start_b);
            CHECK(up != 0);
            if (up > start_b && up - start_b > worst)
                worst = up - start_b;
        }
    }
    /* Not a promise the design makes, but a check that nothing is badly
     * off: a crossed cable works at once, the other kind after a swap or
     * a few. */
    CHECK(worst < 6000);
    printf("  link: slowest of 600 pairs was up %u ms after the second port started\n",
           (unsigned)worst);
}

/* The same seed on both ends: they would swap in step for ever if the two
 * ends drew the same dwell times. Their starts differ, though, and that is
 * enough to break the tie. */
static void same_seed(void)
{
    CHECK(run_pair(12345, 12345, false, 3) != 0);
    CHECK(run_pair(12345, 12345, true, 3) != 0);
}

/* ---------------------------------------------------------------- the rate ---
 *
 * Two ports on one cable, each running its link and saying its HELLOs. A
 * HELLO arrives when the cable is the right way round, the listener's rate
 * is the one it was sent at, the cable carries that rate in that direction,
 * and the test has not asked for it to be lost. */
#define PAIR_LOG  64
#define PAIR_WAIT 32

typedef struct {
    link_t l;
    rng_t r;
    uint32_t id;
    bool old;                   /* a program from before the two rates: its HELLO is the short one */
    bool gone;                  /* unplugged */
    uint32_t stalled_until;     /* busy before then: what arrives waits */
    bool tick_first;            /* after that, its timers run before it hears what waited */
    uint8_t wait[PAIR_WAIT][HELLO_LEN];
    uint8_t wait_len[PAIR_WAIT];
    int waiting;
    unsigned asks, gos, downs, swaps;
    uint32_t said_at;           /* its last HELLO */
} end_t;

typedef struct {
    end_t e[2];
    bool fast_ok[2];            /* what end i says at a faster rate arrives */
    bool loose;                 /* an end restarts or is spoken for by hand: frames may be lost */
    int lose_from;              /* lose HELLOs from this end (-1: none) ... */
    uint8_t lose_flags;         /* ... that carry just these of ASK, GO and BACK ... */
    uint8_t lose_rate;          /* ... at this rate ... */
    int lose_left;              /* ... this many more */
    struct {
        uint32_t t;
        uint8_t from, flags;
        bool arrived;
    } log[PAIR_LOG];            /* the first HELLOs said */
    int logged;
} pair_t;

static void pair_init(pair_t *p, uint32_t id_a, uint32_t id_b)
{
    memset(p, 0, sizeof *p);
    p->e[0].id = id_a;
    p->e[1].id = id_b;
    for (int i = 0; i < 2; i++) {
        rng_seed(&p->e[i].r, p->e[i].id);
        link_init(&p->e[i].l, &p->e[i].r, 0);
        p->fast_ok[i] = true;
    }
    p->e[0].l.swapped = true;   /* the way round that works */
    p->lose_from = -1;
}

static void pair_hear(end_t *e, const uint8_t *pl, uint8_t len, uint32_t t)
{
    link_heard(&e->l, t);
    link_hello(&e->l, pl, len, e->id, t);
}

static void pair_ms(pair_t *p, uint32_t t)
{
    for (int i = 0; i < 2; i++) {
        end_t *e = &p->e[i];

        if (e->gone || t < e->stalled_until)
            continue;
        unsigned ev = 0;
        if (e->tick_first)
            ev = link_tick(&e->l, &e->r, t);
        for (int k = 0; k < e->waiting; k++)
            pair_hear(e, e->wait[k], e->wait_len[k], t);
        e->waiting = 0;
        if (!e->tick_first)
            ev = link_tick(&e->l, &e->r, t);
        e->downs += (ev & LINK_WENT_DOWN) != 0;
        e->swaps += (ev & LINK_SWAPPED) != 0;
    }

    /* A HELLO is answered well inside a millisecond. */
    for (int round = 0; round < 8; round++) {
        for (int i = 0; i < 2; i++) {
            end_t *s = &p->e[i], *d = &p->e[!i];
            uint8_t pl[HELLO_LEN], rate = s->l.rate, flags, len;
            bool arrives;

            if (s->gone || t < s->stalled_until || !s->l.hello_due)
                continue;
            link_hello_out(&s->l, pl, KIND_TILE, s->id, PORT_E, t);
            CHECK(!s->l.hello_due);
            flags = pl[7];
            len = s->old ? HELLO_MIN_LEN : HELLO_LEN;
            CHECK_EQ(HELLO_NOW(flags), rate);
            s->said_at = t;
            s->asks += rate == RATE_BASE && (flags & HELLO_ASK);
            s->gos += (flags & HELLO_GO) != 0;

            arrives = !d->gone && s->l.swapped != d->l.swapped && d->l.rate == rate &&
                      (rate == RATE_BASE || p->fast_ok[i]);
            if (arrives && i == p->lose_from && p->lose_left && rate == p->lose_rate &&
                (flags & (HELLO_ASK | HELLO_GO | HELLO_BACK)) == p->lose_flags) {
                p->lose_left--;
                arrives = false;
            }
            if (p->logged < PAIR_LOG) {
                p->log[p->logged].t = t;
                p->log[p->logged].from = (uint8_t)i;
                p->log[p->logged].flags = flags;
                p->log[p->logged].arrived = arrives;
                p->logged++;
            }
            if (!arrives)
                continue;
            if (t < d->stalled_until) {
                if (d->waiting < PAIR_WAIT) {
                    memcpy(d->wait[d->waiting], pl, HELLO_LEN);
                    d->wait_len[d->waiting++] = len;
                }
            } else {
                pair_hear(d, pl, len, t);
            }
        }
    }

    /* An end that may send more than HELLOs is heard: the other end is at
     * the same rate. */
    if (!p->loose && !p->e[0].gone && !p->e[1].gone) {
        for (int i = 0; i < 2; i++) {
            if (!link_holds(&p->e[i].l) && p->e[i].l.rate != p->e[!i].l.rate)
                CHECK_EQ(p->e[!i].l.rate, p->e[i].l.rate);
        }
    }
}

static bool pair_fast(const pair_t *p)
{
    return p->e[0].l.rate_state == RS_FAST && p->e[1].l.rate_state == RS_FAST &&
           p->e[0].l.rate == RATE_4M && p->e[1].l.rate == RATE_4M;
}

static bool pair_base(const pair_t *p)
{
    return p->e[0].l.rate_state == RS_BASE && p->e[1].l.rate_state == RS_BASE &&
           p->e[0].l.rate == RATE_BASE && p->e[1].l.rate == RATE_BASE;
}

/* Run from `from` to `to`, both included. Returns when both ends first were
 * at the faster rate and sure of it, or 0. */
static uint32_t pair_run(pair_t *p, uint32_t from, uint32_t to)
{
    uint32_t fast_at = 0;

    for (uint32_t t = from; t <= to; t++) {
        pair_ms(p, t);
        if (!fast_at && pair_fast(p))
            fast_at = t;
    }
    return fast_at;
}

static void rate_agreed(void)
{
    for (int lower = 0; lower < 2; lower++) {
        pair_t p;
        const end_t *lead = &p.e[lower], *other = &p.e[!lower];
        int go = -1, call = -1;

        pair_init(&p, lower == 0 ? 0x100 : 0x200, lower == 0 ? 0x200 : 0x100);
        CHECK_EQ(p.e[0].l.rate, RATE_BASE);     /* every port starts at the base rate */
        uint32_t fast_at = pair_run(&p, 1, 3000);

        CHECK(fast_at != 0 && fast_at < 20);
        CHECK(pair_fast(&p));
        CHECK_EQ(lead->asks, 1);                /* the lower ID asks, once */
        CHECK_EQ(other->asks, 0);
        CHECK_EQ(lead->gos, 0);
        CHECK_EQ(other->gos, 1);
        CHECK_EQ(p.e[0].downs + p.e[1].downs, 0);
        CHECK_EQ(p.e[0].swaps + p.e[1].swaps, 0);
        CHECK_EQ(p.e[0].l.rate_fails + p.e[1].l.rate_fails, 0);
        CHECK(p.e[0].l.proven && p.e[1].l.proven);
        CHECK_EQ(link_slow(&p.e[0].l), SLOW_NOT);
        CHECK(!link_holds(&p.e[0].l) && !link_holds(&p.e[1].l));

        /* The GO is the last thing its sender says at the base rate, and it
         * says nothing more until it is called at the new one; the call
         * leaves the other end time to get there. */
        for (int i = 0; i < p.logged; i++) {
            if (p.log[i].flags & HELLO_GO)
                go = i;
            else if (go >= 0 && call < 0 && HELLO_NOW(p.log[i].flags) == RATE_4M)
                call = i;
        }
        CHECK(go >= 0 && call == go + 1);
        if (go >= 0 && call > go) {
            CHECK_EQ(p.log[go].from, !lower);
            CHECK_EQ(HELLO_NOW(p.log[go].flags), RATE_BASE);
            CHECK_EQ(HELLO_CAN(p.log[go].flags), RATE_4M);
            CHECK_EQ(p.log[call].from, lower);
            CHECK(p.log[call].flags & HELLO_ASK);
            CHECK(p.log[call].t - p.log[go].t >= RATE_GUARD_MS);
        }
        /* after that, a HELLO every 100 ms as before */
        CHECK(p.logged < 20 + 2 * 31);
    }
}

/* A neighbour whose program is from before the two rates: its HELLO has no
 * last byte. Nobody asks, and the link is what it always was. */
static void rate_old_neighbour(void)
{
    for (int old = 0; old < 2; old++) {
        pair_t p;

        pair_init(&p, 0x100, 0x200);
        p.e[old].old = true;
        p.e[old].l.rate_max = RATE_BASE;
        CHECK_EQ(pair_run(&p, 1, 5000), 0);
        CHECK(pair_base(&p));
        CHECK(p.e[0].l.up && p.e[1].l.up);
        CHECK_EQ(p.e[0].asks + p.e[1].asks, 0);
        CHECK_EQ(p.e[0].downs + p.e[1].downs, 0);
        CHECK_EQ(link_slow(&p.e[!old].l), SLOW_NBR);
        CHECK_EQ(link_slow(&p.e[old].l), SLOW_NOT);
    }
}

/* A cable that does not carry the faster rate, one way or both: each try
 * costs RATE_TRY_MS at most and never the link, and after RATE_TRIES of
 * them nobody tries again. */
static void rate_fails_and_gives_up(void)
{
    for (int kind = 0; kind < 3; kind++) {
        pair_t p;
        uint32_t away = 0, longest = 0;

        pair_init(&p, 0x100, 0x200);
        p.fast_ok[0] = kind == 1;
        p.fast_ok[1] = kind == 2;
        for (uint32_t t = 1; t <= RATE_RETRY_MS * (RATE_TRIES + 4); t++) {
            pair_ms(&p, t);
            CHECK(!pair_fast(&p));
            away = pair_base(&p) ? 0 : away + 1;
            if (away > longest)
                longest = away;
        }
        CHECK(pair_base(&p));
        CHECK(longest > RATE_TRY_MS / 2 && longest <= RATE_TRY_MS + RATE_HOLD_MS + 2);
        CHECK_EQ(p.e[0].asks, RATE_TRIES);
        CHECK_EQ(p.e[1].gos, RATE_TRIES);
        CHECK_EQ(p.e[0].downs + p.e[1].downs, 0);
        CHECK_EQ(p.e[0].swaps + p.e[1].swaps, 0);
        CHECK_EQ(link_slow(&p.e[0].l), SLOW_GAVE_UP);
        CHECK_EQ(link_slow(&p.e[1].l), SLOW_GAVE_UP);

        /* the tries were RATE_RETRY_MS apart */
        uint32_t last_ask = 0;
        for (int i = 0; i < p.logged; i++) {
            if (!(p.log[i].flags & HELLO_ASK) || HELLO_NOW(p.log[i].flags) != RATE_BASE)
                continue;
            if (last_ask)
                CHECK(p.log[i].t - last_ask >= RATE_RETRY_MS);
            last_ask = p.log[i].t;
        }

        /* Another neighbour on the port: all is forgotten. */
        uint8_t pl[HELLO_LEN];
        link_hello_payload(pl, KIND_TILE, 0x300, PORT_W);
        pl[7] = RATE_4M;
        CHECK_EQ(link_hello(&p.e[0].l, pl, HELLO_LEN, p.e[0].id, 20000), LINK_NBR);
        CHECK_EQ(p.e[0].l.rate_fails, 0);
        CHECK_EQ(link_slow(&p.e[0].l), SLOW_NOT);
    }
}

/* One HELLO of the exchange is lost. */
static void rate_lost_hello(void)
{
    static const struct {
        int from;
        uint8_t flags, rate;
        int count;
        bool again;         /* it takes a second asking */
        uint8_t fails;      /* what the asked end counts */
    } lose[] = {
        { 0, HELLO_ASK, RATE_BASE, 1, true, 0 },    /* the asking */
        { 1, HELLO_GO, RATE_BASE, 1, true, 1 },     /* the GO: the asked end went, and nobody called */
        { 0, HELLO_ASK, RATE_4M, 3, false, 0 },     /* calls */
        { 1, HELLO_ASK, RATE_4M, 2, false, 0 },     /* answers to them */
        { 0, 0, RATE_4M, 1, false, 0 },             /* "I heard your answer" */
    };

    for (size_t k = 0; k < sizeof lose / sizeof lose[0]; k++) {
        pair_t p;

        pair_init(&p, 0x100, 0x200);
        p.lose_from = lose[k].from;
        p.lose_flags = lose[k].flags;
        p.lose_rate = lose[k].rate;
        p.lose_left = lose[k].count;

        uint32_t t = RATE_RETRY_MS - 500;
        uint32_t fast_at = pair_run(&p, 1, t);
        CHECK_EQ(p.lose_left, 0);
        if (!lose[k].again) {
            CHECK(fast_at != 0 && fast_at < 30);
            CHECK_EQ(p.e[0].asks, 1);
        } else {
            /* At the base rate, the link up all the while. The asker never
             * left it, and has nothing against the cable. */
            CHECK_EQ(fast_at, 0);
            CHECK(pair_base(&p));
            CHECK_EQ(p.e[0].l.rate_fails, 0);
            CHECK_EQ(link_slow(&p.e[0].l), SLOW_NOT);
            CHECK_EQ(p.e[1].l.rate_fails, lose[k].fails);
            CHECK_EQ(link_slow(&p.e[1].l), lose[k].fails ? SLOW_RETRY : SLOW_NOT);
            fast_at = pair_run(&p, t + 1, t + 700);
            t += 700;
            CHECK(fast_at >= RATE_RETRY_MS);
            CHECK_EQ(p.e[0].asks, 2);
        }
        /* a try that worked wipes the slate */
        pair_run(&p, t + 1, t + RATE_PROVEN_MS + 10);
        CHECK(pair_fast(&p));
        CHECK_EQ(p.e[0].l.rate_fails + p.e[1].l.rate_fails, 0);
        CHECK_EQ(p.e[0].downs + p.e[1].downs, 0);
    }
}

/* The asked end is busy (erasing flash, say) when it is asked. It does not
 * answer an asking that old with a GO: the asker may have stopped waiting.
 * The asker holds its frames back until the other end says where it is,
 * counts no try that failed, and asks again later. */
static void rate_asked_while_busy(void)
{
    /* whichever the program does first when it goes on: hear, or run its timers */
    for (int tick_first = 0; tick_first < 2; tick_first++) {
        pair_t p;

        pair_init(&p, 0x100, 0x200);
        p.e[1].tick_first = tick_first;
        p.e[0].l.rate_max = RATE_BASE;              /* not yet */
        pair_run(&p, 1, 10);
        p.e[1].stalled_until = 50;
        pair_run(&p, 11, 11);
        p.e[0].l.rate_max = RATE_4M;
        CHECK_EQ(pair_run(&p, 12, 49), 0);
        CHECK_EQ(p.e[0].asks, 1);
        CHECK_EQ(p.e[0].l.rate_state, RS_BACK);     /* "where are you?" */
        CHECK(link_holds(&p.e[0].l));
        CHECK(p.e[1].waiting > 10);
        pair_ms(&p, 50);
        CHECK(pair_base(&p));                       /* "here", and no GO */
        CHECK_EQ(pair_run(&p, 51, RATE_RETRY_MS), 0);
        CHECK_EQ(p.e[1].gos, 0);
        CHECK(pair_base(&p));
        CHECK_EQ(p.e[0].l.rate_fails, 0);
        CHECK_EQ(p.e[1].l.rate_fails, 0);
        CHECK(pair_run(&p, RATE_RETRY_MS + 1, RATE_RETRY_MS + 100) != 0);   /* asked again */
        CHECK_EQ(p.e[0].asks, 2);
    }
}

/* A GO that comes after the asker gave up waiting is followed all the
 * same: its sender is at the new rate, and hears nothing at the old. */
static void rate_late_go(void)
{
    pair_t p;
    uint8_t pl[HELLO_LEN];

    pair_init(&p, 0x100, 0x200);
    p.e[1].l.rate_max = RATE_BASE;
    pair_run(&p, 1, 10);
    p.lose_from = 0;                            /* B never hears the asking */
    p.lose_flags = HELLO_ASK;
    p.lose_rate = RATE_BASE;
    p.lose_left = 1;
    p.e[1].l.rate_max = RATE_4M;
    pair_run(&p, 11, 111);                      /* B's next HELLO says so */
    CHECK_EQ(p.e[0].asks, 1);
    pair_run(&p, 112, 130);
    CHECK_EQ(p.e[0].l.rate_state, RS_BASE);
    CHECK_EQ(p.e[0].l.rate_fails, 0);

    link_hello_payload(pl, KIND_TILE, 0x200, PORT_E);
    pl[7] = RATE_4M | HELLO_GO;
    pair_hear(&p.e[0], pl, HELLO_LEN, 130);
    CHECK_EQ(p.e[0].l.rate, RATE_4M);
    CHECK_EQ(p.e[0].l.rate_state, RS_CALL);
    CHECK(link_holds(&p.e[0].l));
}

/* Run until both ends have gone to the faster rate. Returns the time. */
static uint32_t pair_until_tried(pair_t *p)
{
    uint32_t t = 1;

    while (t < 1000 && !(p->e[0].l.rate == RATE_4M && p->e[1].l.rate == RATE_4M))
        pair_ms(p, t++);
    CHECK(t < 1000);
    return t;
}

/* After a try that failed, the two ends are back at the base rate at
 * different moments. Neither sends more than HELLOs before it knows the
 * other is there (pair_ms() checks that), whichever comes late, and when a
 * HELLO of that is lost. */
static void rate_back_together(void)
{
    for (int kind = 0; kind < 6; kind++) {
        pair_t p;
        int who = kind & 1;
        uint32_t extra = 0;

        pair_init(&p, 0x100, 0x200);
        p.fast_ok[0] = p.fast_ok[1] = false;
        uint32_t from = pair_until_tried(&p), t = from;
        switch (kind / 2) {
        case 0:     /* one end is busy when the try ends, until 30 ms after it */
            extra = 30;
            p.e[who].stalled_until = from + RATE_TRY_MS + extra;
            break;
        case 1:     /* its first two "I am back" are lost */
            p.lose_from = who;
            p.lose_flags = HELLO_BACK;
            p.lose_rate = RATE_BASE;
            p.lose_left = 2;
            break;
        default:    /* its next plain HELLO is: an answer to the other's "I am back", if it gives one */
            p.lose_from = who;
            p.lose_flags = 0;
            p.lose_rate = RATE_BASE;
            p.lose_left = 1;
            break;
        }
        while (t < from + 400 && !pair_base(&p))
            pair_ms(&p, t++);
        CHECK(pair_base(&p));
        CHECK(t - from >= RATE_TRY_MS - 2 && t - from <= RATE_TRY_MS + extra + 8);
        CHECK_EQ(p.e[0].l.rate_fails, 1);
        CHECK_EQ(p.e[1].l.rate_fails, 1);
        pair_run(&p, t, t + 500);
        CHECK(pair_base(&p));
        CHECK_EQ(p.e[0].downs + p.e[1].downs, 0);
        CHECK_EQ(p.e[0].asks, 1);
    }
}

/* ... and when the other end never says so, because it is gone: not held
 * back for ever. */
static void rate_back_alone(void)
{
    pair_t p;

    pair_init(&p, 0x100, 0x200);
    p.fast_ok[0] = p.fast_ok[1] = false;
    uint32_t from = pair_until_tried(&p), t = from;
    p.e[1].gone = true;
    while (t < from + 1000 && link_holds(&p.e[0].l))
        pair_ms(&p, t++);
    CHECK_NEAR(t - from, RATE_TRY_MS + RATE_BACK_MS, 3);
    CHECK_EQ(p.e[0].l.rate_state, RS_BASE);
    CHECK_EQ(p.e[0].l.rate, RATE_BASE);
    CHECK_EQ(p.e[0].l.rate_fails, 1);
    CHECK(p.e[0].l.up);                     /* the link is the next to go */
    pair_run(&p, t, t + LINK_UP_MS);
    CHECK_EQ(p.e[0].downs, 1);
}

/* The neighbour goes away from a link at the faster rate: back to the base
 * rate, and a whole dwell of listening there before the pins are turned. */
static void rate_down(void)
{
    for (int young = 0; young < 2; young++) {
        pair_t p;
        uint32_t gone_at = young ? 300 : 3000, down_at = 0, swap_at = 0;

        pair_init(&p, 0x100, 0x200);
        CHECK(pair_run(&p, 1, gone_at) != 0);
        p.e[1].gone = true;
        for (uint32_t t = gone_at + 1; t <= gone_at + 2000; t++) {
            pair_ms(&p, t);
            if (p.e[0].downs && !down_at) {
                down_at = t;
                CHECK_EQ(p.e[0].l.rate, RATE_BASE);
                CHECK_EQ(p.e[0].l.rate_state, RS_BASE);
                CHECK_EQ(p.e[0].said_at, t);    /* and says so there at once */
            }
            if (p.e[0].swaps && !swap_at)
                swap_at = t;
        }
        CHECK(down_at != 0 && down_at <= gone_at + LINK_UP_MS + 1);
        CHECK(swap_at >= down_at + DWELL_MIN_MS);
        /* a link that went that soon after the change counts against the rate */
        CHECK_EQ(p.e[0].l.rate_fails, young ? 1 : 0);
    }
}

/* After a pause in which nobody sent, every port is at the base rate. */
static void rate_restart(void)
{
    pair_t p;

    pair_init(&p, 0x100, 0x200);
    CHECK(pair_run(&p, 1, 500) != 0);
    link_restart(&p.e[0].l, &p.e[0].r, 501);
    link_restart(&p.e[1].l, &p.e[1].r, 501);
    CHECK(pair_base(&p));
    CHECK(!p.e[0].l.up);
    CHECK(pair_run(&p, 502, 700) != 0);         /* and they agree again */
}

/* A HELLO that was sent at another rate than the port's now is: it waited
 * in the UART while the rate changed, and says nothing about rates. */
static void rate_stale_hello(void)
{
    pair_t p;
    uint8_t pl[HELLO_LEN];

    pair_init(&p, 0x100, 0x200);
    CHECK(pair_run(&p, 1, 500) != 0);
    link_t before = p.e[1].l;
    link_hello_payload(pl, KIND_TILE, 0x100, PORT_E);
    pl[7] = RATE_4M | HELLO_ASK;                /* "let us go faster", said at the base rate */
    link_hello(&p.e[1].l, pl, HELLO_LEN, 0x200, 500);
    CHECK(memcmp(&before, &p.e[1].l, sizeof before) == 0);
    pl[7] = RATE_4M | HELLO_GO;
    link_hello(&p.e[1].l, pl, HELLO_LEN, 0x200, 500);
    CHECK(memcmp(&before, &p.e[1].l, sizeof before) == 0);

    /* and at the base rate, a call from the faster one */
    pair_init(&p, 0x100, 0x200);
    p.e[0].l.rate_max = RATE_BASE;              /* so that nothing happens by itself */
    pair_run(&p, 1, 50);
    CHECK(pair_base(&p));
    before = p.e[1].l;
    pl[7] = RATE_4M | (RATE_4M << 2) | HELLO_ASK;
    link_hello(&p.e[1].l, pl, HELLO_LEN, 0x200, 50);
    CHECK(memcmp(&before, &p.e[1].l, sizeof before) == 0);
}

int main(void)
{
    RUN(starts_straight);
    RUN(hello_every_100ms);
    RUN(swaps_when_deaf);
    RUN(up_and_down);
    RUN(hello_rules);
    RUN(finds_the_cable);
    RUN(same_seed);
    RUN(rate_agreed);
    RUN(rate_old_neighbour);
    RUN(rate_fails_and_gives_up);
    RUN(rate_lost_hello);
    RUN(rate_asked_while_busy);
    RUN(rate_late_go);
    RUN(rate_back_together);
    RUN(rate_back_alone);
    RUN(rate_down);
    RUN(rate_restart);
    RUN(rate_stale_hello);
    return T_DONE();
}
