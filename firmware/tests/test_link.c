/* link.c: HELLO, up and down, and finding which way round the cable is. */
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
    CHECK_EQ(link_hello(&l, pl, HELLO_LEN, 0x11111111u), LINK_NBR);
    CHECK_EQ(l.nbr_id, 0xABCD1234u);
    CHECK_EQ(l.nbr_port, PORT_E);
    CHECK_EQ(l.nbr_kind, KIND_TILE);
    CHECK_EQ(link_hello(&l, pl, HELLO_LEN, 0x11111111u), 0);    /* nothing new */

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
    CHECK_EQ(link_hello(&l, pl, HELLO_LEN - 1, 1), 0);          /* too short */
    pl[0] = PROTO_VERSION + 1;
    CHECK_EQ(link_hello(&l, pl, HELLO_LEN, 1), 0);              /* another version */
    pl[0] = PROTO_VERSION;
    CHECK_EQ(link_hello(&l, pl, HELLO_LEN, 0x22222222u), 0);    /* ourselves */
    CHECK_EQ(l.nbr_id, ID_NONE);

    link_hello_payload(pl, KIND_TILE, ID_NONE, 0);
    CHECK_EQ(link_hello(&l, pl, HELLO_LEN, 1), 0);
    link_hello_payload(pl, KIND_TILE, ID_ALL, 0);
    CHECK_EQ(link_hello(&l, pl, HELLO_LEN, 1), 0);

    link_hello_payload(pl, KIND_HUB, 0x22222222u, 0);
    CHECK_EQ(link_hello(&l, pl, HELLO_LEN, 1), LINK_NBR);
    CHECK_EQ(l.nbr_kind, KIND_HUB);
    /* the same node on another of its ports is a change */
    link_hello_payload(pl, KIND_HUB, 0x22222222u, 2);
    CHECK_EQ(link_hello(&l, pl, HELLO_LEN, 1), LINK_NBR);
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

int main(void)
{
    RUN(starts_straight);
    RUN(hello_every_100ms);
    RUN(swaps_when_deaf);
    RUN(up_and_down);
    RUN(hello_rules);
    RUN(finds_the_cable);
    RUN(same_seed);
    return T_DONE();
}
