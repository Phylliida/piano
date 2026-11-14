/* keys.c: from ADC values to notes. The "sensor" here is the same model the
 * table was made from (lut_field), so a key moved in a straight line should
 * come out with the speed it was moved at. */
#include "keys.h"
#include "lut.h"
#include "t.h"
#include "util.h"

#define HZ      2000u
#define FULL    65535

typedef struct {
    uint8_t key;
    bool on;
    uint16_t speed;
    uint32_t scan;
} event_t;

typedef struct {
    keys_t k;
    uint32_t scan;
    int rest[KEYS_N], swing[KEYS_N];    /* the sensors: ADC counts */
    int x[KEYS_N];                      /* where each key is, 0..FULL */
    int noise;                          /* +- this many counts on every reading */
    rng_t rng;
    event_t ev[64];
    int nev;
} rig_t;

static void on_note(void *ctx, uint8_t key, bool on, uint16_t speed)
{
    rig_t *r = ctx;

    if (r->nev < 64)
        r->ev[r->nev] = (event_t){key, on, speed, r->scan};
    r->nev++;
}

/* One scan with the keys where they are. `skip` scans were lost before it. */
static void scan_skipping(rig_t *r, uint32_t skip)
{
    uint16_t adc[KEYS_N];

    for (int i = 0; i < KEYS_N; i++) {
        int v = r->rest[i] + r->swing[i] * (int)lut_apply(lut_field, (uint16_t)r->x[i]) / FULL;
        if (r->noise)
            v += (int)rng_range(&r->rng, 0, 2 * (uint32_t)r->noise) - r->noise;
        adc[i] = (uint16_t)(v < 0 ? 0 : v > 4095 ? 4095 : v);
    }
    r->scan += 1 + skip;
    keys_scan(&r->k, r->scan, adc);
}

static void scan(rig_t *r)
{
    scan_skipping(r, 0);
}

static void wait(rig_t *r, uint32_t scans)
{
    while (scans--)
        scan(r);
}

/* Move one key in a straight line to `to`, taking `scans` scans. */
static void move(rig_t *r, int key, int to, uint32_t scans)
{
    int from = r->x[key];

    for (uint32_t i = 1; i <= scans; i++) {
        r->x[key] = from + (int)((int64_t)(to - from) * i / scans);
        scan(r);
    }
    r->x[key] = to;
}

/* A rig with every key at rest and warmed up. `measured`: the swings are
 * known to the firmware, as if loaded from flash. */
static void rig_init(rig_t *r, int swing, bool measured)
{
    key_cal_t cal[KEYS_N];

    memset(r, 0, sizeof *r);
    rng_seed(&r->rng, 1234);
    keys_init(&r->k, HZ, on_note, r);
    for (int i = 0; i < KEYS_N; i++) {
        r->rest[i] = 1900 + 7 * i;
        r->swing[i] = swing;
        cal[i].rest = 1000;                     /* only the difference counts */
        cal[i].bottom = (uint16_t)(1000 + swing);
    }
    if (measured)
        keys_load_cal(&r->k, cal);
    wait(r, KEYS_WARMUP);
}

static int pct(int percent)
{
    return FULL * percent / 100;
}

/* ---- tests ---- */

static void warm_up(void)
{
    rig_t r;

    memset(&r, 0, sizeof r);
    keys_init(&r.k, HZ, on_note, &r);
    for (int i = 0; i < KEYS_N; i++) {
        r.rest[i] = 500 + 80 * i;
        r.swing[i] = 600;
    }
    CHECK(!keys_ready(&r.k));
    for (int i = 0; i < KEYS_WARMUP; i++) {
        CHECK(!keys_ready(&r.k));
        scan(&r);
    }
    CHECK(keys_ready(&r.k));
    for (int i = 0; i < KEYS_N; i++)
        CHECK_NEAR(r.k.key[i].rest >> 16, r.rest[i], 1);

    wait(&r, 4000);
    CHECK_EQ(r.nev, 0);
    for (uint8_t i = 0; i < KEYS_N; i++) {
        CHECK(!keys_on(&r.k, i));
        CHECK_EQ(r.k.key[i].x, 0);
    }

    /* the same scan handed over twice counts once */
    uint16_t adc[KEYS_N] = {0};
    uint32_t before = r.k.last_scan;
    int32_t f = r.k.key[0].f;
    keys_scan(&r.k, before, adc);
    CHECK_EQ(r.k.key[0].f, f);
}

/* Press and release in a straight line over `ms`; returns the two speeds. */
static void press_release(rig_t *r, int key, uint32_t scans, uint16_t *down, uint16_t *up)
{
    int first = r->nev;

    move(r, key, FULL, scans);
    wait(r, 200);
    CHECK_EQ(r->nev, first + 1);
    CHECK(keys_on(&r->k, (uint8_t)key));
    move(r, key, 0, scans);
    wait(r, 200);
    CHECK_EQ(r->nev, first + 2);
    CHECK(!keys_on(&r->k, (uint8_t)key));
    if (r->nev != first + 2 || r->nev > 64) {
        *down = *up = 0;
        return;
    }
    CHECK_EQ(r->ev[first].key, key);
    CHECK(r->ev[first].on);
    CHECK_EQ(r->ev[first + 1].key, key);
    CHECK(!r->ev[first + 1].on);
    *down = r->ev[first].speed;
    *up = r->ev[first + 1].speed;
}

static void speeds(void)
{
    /* full travel in this many scans; the speed is 100 / seconds */
    static const struct { uint32_t scans; int percent; } cases[] = {
        {4000, 3},      /* 2 s */
        {2000, 3},      /* 1 s */
        {200, 3},       /* 100 ms */
        {40, 4},        /* 20 ms */
        {20, 6},        /* 10 ms: lo to hi is 9 scans */
        {10, 12},       /* 5 ms: 4.5 scans */
        {6, 25},        /* 3 ms: 2.7 scans, about as hard as a key can be hit */
    };
    rig_t r;
    uint16_t down, up, last_down = 0;

    rig_init(&r, 700, true);
    for (size_t c = 0; c < sizeof cases / sizeof cases[0]; c++) {
        int want = (int)(100u * HZ / cases[c].scans);
        press_release(&r, 5, cases[c].scans, &down, &up);
        CHECK_NEAR(down, want, want * cases[c].percent / 100);
        CHECK_NEAR(up, want, want * cases[c].percent / 100);
        CHECK(down > last_down);                /* faster is always faster */
        last_down = down;
        printf("  keys: %5u scans: want %5d, on %5u, off %5u\n",
               (unsigned)cases[c].scans, want, down, up);
    }

    /* slower than can be counted is still not 0 */
    move(&r, 5, pct(16), 10);
    wait(&r, 5000000u / 4);                     /* DT_MAX at a time would be slow: just wait */
    r.nev = 0;
    move(&r, 5, FULL, 3000000);
    CHECK_EQ(r.nev, 1);
    CHECK(r.ev[0].speed >= 1);
    CHECK(r.ev[0].speed <= 2);
}

/* All keys are their own: a press on one says nothing about the others. */
static void keys_are_independent(void)
{
    rig_t r;

    rig_init(&r, 700, true);
    for (int key = 0; key < KEYS_N; key++) {
        uint16_t down, up;
        r.nev = 0;
        press_release(&r, key, 100, &down, &up);
        CHECK_NEAR(down, 2000, 60);
        CHECK_NEAR(up, 2000, 60);
    }
}

static void aborted_press(void)
{
    rig_t r;

    rig_init(&r, 700, true);
    move(&r, 3, pct(40), 100);
    CHECK_EQ(r.k.key[3].state, KEY_DOWN);
    wait(&r, 100);
    move(&r, 3, 0, 100);
    CHECK_EQ(r.k.key[3].state, KEY_IDLE);
    move(&r, 3, pct(59), 100);
    move(&r, 3, pct(5), 100);
    wait(&r, 100);
    CHECK_EQ(r.nev, 0);

    /* and the next real press is timed from its own start, not the old one */
    wait(&r, 3000);
    move(&r, 3, FULL, 200);
    CHECK_EQ(r.nev, 1);
    CHECK_NEAR(r.ev[0].speed, 1000, 30);

    /* hovering about `lo` does not make notes either */
    r.nev = 0;
    move(&r, 3, 0, 200);
    r.nev = 0;
    for (int i = 0; i < 50; i++) {
        move(&r, 3, pct(17), 10);
        move(&r, 3, pct(12), 10);
    }
    CHECK_EQ(r.nev, 0);
}

static void retrigger(void)
{
    rig_t r;

    rig_init(&r, 700, true);
    move(&r, 7, FULL, 100);
    wait(&r, 100);
    CHECK_EQ(r.nev, 1);

    /* up to 0.30 (release is 0.50, so 0.20 above the turning point: more
     * than `retrigger`), and down again: the note ends and a new one starts */
    move(&r, 7, pct(30), 140);                  /* 0.7 of travel in 70 ms: speed 1000 */
    CHECK_EQ(r.k.key[7].state, KEY_UP);
    CHECK_EQ(r.nev, 1);
    move(&r, 7, FULL, 70);                      /* 0.7 in 35 ms: speed 2000 */
    CHECK_EQ(r.nev, 3);
    CHECK(!r.ev[1].on);
    CHECK(r.ev[2].on);
    CHECK_EQ(r.ev[1].scan, r.ev[2].scan);       /* off and on at the same moment */
    CHECK_NEAR(r.ev[1].speed, 1000, 60);
    CHECK_NEAR(r.ev[2].speed, 2000, 120);
    CHECK(keys_on(&r.k, 7));

    /* up only to 0.45: not far enough. The note goes on as if nothing happened */
    move(&r, 7, pct(45), 100);
    CHECK_EQ(r.k.key[7].state, KEY_UP);
    move(&r, 7, FULL, 100);
    CHECK_EQ(r.k.key[7].state, KEY_HELD);
    CHECK_EQ(r.nev, 3);

    /* up to between release and hi: never left HELD */
    move(&r, 7, pct(55), 100);
    CHECK_EQ(r.k.key[7].state, KEY_HELD);
    move(&r, 7, FULL, 100);
    CHECK_EQ(r.nev, 3);

    /* with `retrigger` 0 any dip under `release` retriggers; with 1000 none does */
    CHECK(keys_set_param(&r.k, PARAM_RETRIGGER, 0));
    move(&r, 7, pct(48), 100);
    move(&r, 7, FULL, 100);
    CHECK_EQ(r.nev, 5);
    CHECK(keys_set_param(&r.k, PARAM_RETRIGGER, 1000));
    move(&r, 7, pct(20), 100);
    move(&r, 7, FULL, 100);
    CHECK_EQ(r.nev, 5);

    move(&r, 7, 0, 100);
    CHECK_EQ(r.nev, 6);
    CHECK(!r.ev[5].on);
}

/* A sensor whose output falls as the key goes down. */
static void falling_sensor(void)
{
    rig_t r;
    uint16_t down, up;

    rig_init(&r, -650, true);
    press_release(&r, 11, 200, &down, &up);
    CHECK_NEAR(down, 1000, 30);
    CHECK_NEAR(up, 1000, 30);

    /* and unmeasured, by the parameter alone */
    rig_init(&r, -600, false);
    CHECK(keys_set_param(&r.k, PARAM_SWING, -600));
    press_release(&r, 11, 200, &down, &up);
    CHECK_NEAR(down, 1000, 30);

    /* the wrong sign gives no notes at all, however hard the key is pressed */
    rig_init(&r, -600, false);
    move(&r, 11, FULL, 100);
    move(&r, 11, 0, 100);
    CHECK_EQ(r.nev, 0);
}

/* Nobody measured this key: the furthest press so far is taken as bottom. */
static void unmeasured_keys_learn(void)
{
    rig_t r;
    uint16_t down, up;

    rig_init(&r, 900, false);                   /* the nominal swing is 600 */
    CHECK_EQ(keys_measured(&r.k), 0);
    CHECK_EQ(r.k.key[2].swing, 600 * 16);
    press_release(&r, 2, 200, &down, &up);
    CHECK_NEAR(r.k.key[2].swing, 900 * 16, 2 * 16);
    CHECK(down > 1000);                         /* it took bottom to be nearer than it was */
    press_release(&r, 2, 200, &down, &up);
    CHECK_NEAR(down, 1000, 30);
    CHECK_EQ(r.k.key[3].swing, 600 * 16);       /* the others are as they were */
    CHECK_EQ(keys_measured(&r.k), 0);

    /* a sensor weaker than the guess still plays, if not at the right speed */
    rig_init(&r, 450, false);
    press_release(&r, 2, 200, &down, &up);
    CHECK(down > 300 && down < 1000);
}

static void calibration(void)
{
    rig_t r;
    key_cal_t cal[KEYS_N];
    uint16_t down, up;

    rig_init(&r, 600, false);
    for (int i = 0; i < KEYS_N; i++)
        r.swing[i] = i < 10 ? 800 + 10 * i : i < 20 ? -(500 + 10 * i) : 40;

    CHECK_EQ(keys_cal(&r.k, CAL_STOP), 0);      /* not started: nothing */
    CHECK_EQ(keys_cal(&r.k, CAL_START), 0);
    CHECK(r.k.calibrating);
    for (int i = 0; i < 30; i++) {
        move(&r, i, FULL, 60);
        wait(&r, 20);
        move(&r, i, 0, 60);
    }
    wait(&r, 100);
    /* keys 20..29 moved the value by 40 counts: not a press */
    CHECK_EQ(keys_cal(&r.k, CAL_STOP), 20);
    CHECK(!r.k.calibrating);
    CHECK_EQ(keys_measured(&r.k), 20);

    keys_get_cal(&r.k, cal);
    for (int i = 0; i < KEYS_N; i++) {
        CHECK_NEAR(cal[i].rest, r.rest[i], 2);
        if (i < 20)
            CHECK_NEAR((int)cal[i].bottom - (int)cal[i].rest, r.swing[i], 3);
        else
            CHECK_EQ(cal[i].bottom, cal[i].rest);
    }

    /* measured keys play at the right speed, either direction */
    r.nev = 0;
    press_release(&r, 4, 200, &down, &up);
    CHECK_NEAR(down, 1000, 30);
    press_release(&r, 14, 200, &down, &up);
    CHECK_NEAR(down, 1000, 30);

    /* what get_cal gave, loaded into a fresh start, is the same calibration */
    keys_t again;
    keys_init(&again, HZ, NULL, NULL);
    keys_load_cal(&again, cal);
    CHECK_EQ(keys_measured(&again), 20);
    for (int i = 0; i < 20; i++)
        CHECK_NEAR(again.key[i].swing, r.k.key[i].swing, 16);
    CHECK_EQ(again.key[25].swing, 600 * 16);

    /* nonsense from flash is not a calibration */
    key_cal_t bad[KEYS_N];
    memset(bad, 0xFF, sizeof bad);
    bad[0] = (key_cal_t){2000, 2005};
    bad[1] = (key_cal_t){2000, 5000};
    keys_load_cal(&again, bad);
    CHECK_EQ(keys_measured(&again), 0);

    CHECK_EQ(keys_cal(&r.k, CAL_FORGET), 0);
    CHECK_EQ(keys_measured(&r.k), 0);
    CHECK(!r.k.calibrating);
    CHECK_EQ(r.k.key[4].swing, 600 * 16);
}

/* Rest wanders with temperature; notes must not come of it. */
static void rest_drifts(void)
{
    rig_t r;
    uint16_t down, up;

    rig_init(&r, 700, true);
    for (int step = 0; step < 60; step++) {     /* 60 counts in 30 s */
        for (int i = 0; i < KEYS_N; i++)
            r.rest[i] += (i & 1) ? 1 : -1;
        wait(&r, HZ / 2);
    }
    CHECK_EQ(r.nev, 0);
    for (int i = 0; i < KEYS_N; i++) {
        CHECK_NEAR(r.k.key[i].rest >> 16, r.rest[i], 4);
        CHECK_EQ(r.k.key[i].state, KEY_IDLE);
    }
    press_release(&r, 8, 200, &down, &up);
    CHECK_NEAR(down, 1000, 40);
    press_release(&r, 9, 200, &down, &up);
    CHECK_NEAR(down, 1000, 40);

    /* a key held just short of `lo` for a long time is not taken for rest:
     * it is still a little way down afterwards */
    r.nev = 0;
    move(&r, 8, pct(14), 100);
    wait(&r, 20 * HZ);
    CHECK(r.k.key[8].x > pct(10));
    CHECK_EQ(r.nev, 0);
}

/* A key that is down when the tile starts: rest is wrong until the key
 * comes up, and then follows quickly. */
static void held_at_start(void)
{
    rig_t r;
    uint16_t down, up;

    memset(&r, 0, sizeof r);
    keys_init(&r.k, HZ, on_note, &r);
    for (int i = 0; i < KEYS_N; i++) {
        r.rest[i] = 2000;
        r.swing[i] = 600;
    }
    r.x[6] = FULL;
    wait(&r, KEYS_WARMUP + 2000);
    CHECK_EQ(r.nev, 0);
    move(&r, 6, 0, 100);
    wait(&r, HZ);                               /* a second to settle */
    CHECK_EQ(r.nev, 0);
    CHECK_NEAR(r.k.key[6].rest >> 16, 2000, 3);
    press_release(&r, 6, 200, &down, &up);
    CHECK_NEAR(down, 1000, 50);
}

/* Scans that never arrived: the time between the others is still right. */
static void lost_scans(void)
{
    rig_t r;
    int from, to = FULL;

    rig_init(&r, 700, true);
    from = r.x[5];
    for (uint32_t i = 1; i <= 200; i++) {
        r.x[5] = from + (int)((int64_t)(to - from) * i / 200);
        if (i % 3 == 0)
            scan_skipping(&r, 2);               /* scans i-2 and i-1 were lost */
    }
    CHECK_EQ(r.nev, 1);
    CHECK_NEAR(r.ev[0].speed, 1000, 50);

    /* a very long gap does not overflow anything */
    scan_skipping(&r, 0xF0000000u);
    scan_skipping(&r, 0x20000000u);             /* the counter wraps */
    move(&r, 5, 0, 200);
    CHECK_EQ(r.nev, 2);
    CHECK_NEAR(r.ev[1].speed, 1000, 50);
}

static void parameters(void)
{
    rig_t r;
    int32_t p[TILE_PARAMS + 1], d[TILE_PARAMS + 1];

    rig_init(&r, 700, true);
    keys_default_params(d);
    CHECK_EQ(d[PARAM_LO], 150);
    CHECK_EQ(d[PARAM_HI], 600);
    CHECK_EQ(d[PARAM_RELEASE], 500);
    CHECK_EQ(d[PARAM_RETRIGGER], 100);
    CHECK(memcmp(r.k.param, d, sizeof d) == 0);

    /* the levels keep their order: lo < release <= hi */
    CHECK(!keys_set_param(&r.k, PARAM_LO, 500));
    CHECK(!keys_set_param(&r.k, PARAM_LO, 5));
    CHECK(keys_set_param(&r.k, PARAM_LO, 499));
    CHECK(keys_set_param(&r.k, PARAM_LO, 100));
    CHECK(!keys_set_param(&r.k, PARAM_HI, 499));
    CHECK(!keys_set_param(&r.k, PARAM_HI, 991));
    CHECK(keys_set_param(&r.k, PARAM_HI, 500));
    CHECK(keys_set_param(&r.k, PARAM_HI, 800));
    CHECK(!keys_set_param(&r.k, PARAM_RELEASE, 100));
    CHECK(!keys_set_param(&r.k, PARAM_RELEASE, 801));
    CHECK(keys_set_param(&r.k, PARAM_RELEASE, 800));
    CHECK(!keys_set_param(&r.k, PARAM_RETRIGGER, -1));
    CHECK(!keys_set_param(&r.k, PARAM_POS_MODE, 3));
    CHECK(!keys_set_param(&r.k, PARAM_POS_INTERVAL, 0));
    CHECK(!keys_set_param(&r.k, PARAM_RAW_INTERVAL, 1));
    CHECK(!keys_set_param(&r.k, PARAM_SWING, 0));
    CHECK(!keys_set_param(&r.k, PARAM_SWING, 15));
    CHECK(!keys_set_param(&r.k, PARAM_SWING, 4096));
    CHECK(keys_set_param(&r.k, PARAM_SWING, -4095));
    CHECK(!keys_set_param(&r.k, 0, 1));
    CHECK(!keys_set_param(&r.k, TILE_PARAMS + 1, 1));
    CHECK_EQ(r.k.param[PARAM_LO], 100);
    CHECK_EQ(r.k.param[PARAM_HI], 800);
    CHECK_EQ(r.k.param[PARAM_RELEASE], 800);

    /* the new levels are the ones used: the note starts at 0.80 now */
    move(&r, 1, pct(75), 100);
    wait(&r, 50);
    CHECK_EQ(r.nev, 0);
    move(&r, 1, pct(85), 100);
    CHECK_EQ(r.nev, 1);

    /* a set from flash is taken whole or not at all */
    memcpy(p, d, sizeof p);
    p[PARAM_LO] = 200;
    p[PARAM_HI] = 700;
    p[PARAM_RELEASE] = 650;
    CHECK(keys_load_params(&r.k, p));
    CHECK_EQ(r.k.param[PARAM_HI], 700);
    p[PARAM_LO] = 300;
    p[PARAM_POS_MODE] = 9;
    CHECK(!keys_load_params(&r.k, p));
    CHECK_EQ(r.k.param[PARAM_LO], 200);
    p[PARAM_POS_MODE] = 0;
    p[PARAM_RELEASE] = 250;                     /* under lo */
    CHECK(!keys_load_params(&r.k, p));
    CHECK_EQ(r.k.param[PARAM_RELEASE], 650);
}

static void bitmap_and_depth(void)
{
    rig_t r;
    uint8_t bm[5];

    rig_init(&r, 700, true);
    keys_bitmap(&r.k, bm);
    for (int i = 0; i < 5; i++)
        CHECK_EQ(bm[i], 0);

    move(&r, 0, FULL, 50);
    move(&r, 9, pct(80), 50);
    move(&r, 39, FULL, 50);
    move(&r, 20, pct(40), 50);                  /* down a little: not on */
    wait(&r, 20);
    keys_bitmap(&r.k, bm);
    CHECK_EQ(bm[0], 0x01);
    CHECK_EQ(bm[1], 0x02);
    CHECK_EQ(bm[2], 0x00);
    CHECK_EQ(bm[3], 0x00);
    CHECK_EQ(bm[4], 0x80);

    /* mode 0: nothing */
    CHECK_EQ(keys_depth(&r.k, 0), 0);
    /* mode 1: how far past `hi`, of keys that are on */
    CHECK(keys_set_param(&r.k, PARAM_POS_MODE, 1));
    CHECK_EQ(keys_depth(&r.k, 0), 255);
    CHECK_NEAR(keys_depth(&r.k, 9), 127, 3);    /* 0.80 is half way from 0.60 to 1 */
    CHECK_EQ(keys_depth(&r.k, 20), 0);
    CHECK_EQ(keys_depth(&r.k, 1), 0);
    move(&r, 9, pct(55), 50);                   /* still on, but above `hi` */
    CHECK(keys_on(&r.k, 9));
    CHECK_EQ(keys_depth(&r.k, 9), 0);
    /* mode 2: the whole travel of every key */
    CHECK(keys_set_param(&r.k, PARAM_POS_MODE, 2));
    CHECK_EQ(keys_depth(&r.k, 0), 255);
    CHECK_NEAR(keys_depth(&r.k, 9), 140, 3);
    CHECK_NEAR(keys_depth(&r.k, 20), 102, 3);
    CHECK_EQ(keys_depth(&r.k, 1), 0);
}

static void noise(void)
{
    rig_t r;
    uint16_t down, up;

    rig_init(&r, 700, true);
    r.noise = 3;
    wait(&r, 20 * HZ);
    CHECK_EQ(r.nev, 0);
    for (int i = 0; i < 20; i++) {
        press_release(&r, 5, 200, &down, &up);
        CHECK_NEAR(down, 1000, 60);
        CHECK_NEAR(up, 1000, 60);
    }
    /* held at the bottom, and held near a level: no chatter */
    r.nev = 0;
    move(&r, 5, FULL, 100);
    wait(&r, 10 * HZ);
    CHECK_EQ(r.nev, 1);
}

/* Readings from an input that is broken or not there. */
static void wild_readings(void)
{
    keys_t k;
    uint16_t adc[KEYS_N];
    rng_t rng;

    rng_seed(&rng, 5);
    keys_init(&k, HZ, NULL, NULL);
    for (uint32_t s = 1; s < 20000; s++) {
        for (int i = 0; i < KEYS_N; i++)
            adc[i] = (uint16_t)rng_next(&rng);  /* up to 65535: more than the ADC can say */
        keys_scan(&k, s * (1 + (rng_next(&rng) & 3)), adc);
        if (s % 5000 == 0)
            keys_cal(&k, s % 10000 ? CAL_START : CAL_STOP);
    }
    for (uint8_t i = 0; i < KEYS_N; i++) {
        CHECK(k.key[i].swing != 0);
        CHECK(k.key[i].f >= 0 && k.key[i].f <= 4095 * 16);
        (void)keys_depth(&k, i);
    }
}

int main(void)
{
    RUN(warm_up);
    RUN(speeds);
    RUN(keys_are_independent);
    RUN(aborted_press);
    RUN(retrigger);
    RUN(falling_sensor);
    RUN(unmeasured_keys_learn);
    RUN(calibration);
    RUN(rest_drifts);
    RUN(held_at_start);
    RUN(lost_scans);
    RUN(parameters);
    RUN(bitmap_and_depth);
    RUN(noise);
    RUN(wild_readings);
    return T_DONE();
}
