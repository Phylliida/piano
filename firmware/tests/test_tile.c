/* store.c and tile.c: one tile, its hub played by hand on the east port. */
#include "lut.h"
#include "t.h"
#include "tile.h"

#define HZ    2000u
#define HUB   0x48554231u
#define FULL  65535

static const uint8_t UID[12] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12};

/* ---- the rig: a tile, the time, where its keys are, what it sent ---- */

typedef struct {
    uint8_t type, len;
    uint8_t pl[FRAME_MAX_PAYLOAD];
} sent_t;

typedef struct {
    tile_t t;
    uint32_t now;               /* ms */
    uint32_t scan;
    uint16_t seq;
    bool hub_there;
    int x[KEYS_N];
    sent_t out[256];            /* what came out of the east port, HELLOs left out */
    int nout;
} rig_t;

/* What the sensor of a key at travel `x` reads: 2000 at rest, 600 more on
 * the floor (one count less, as the table's last step is never quite taken). */
static uint16_t adc_at(int x)
{
    return (uint16_t)(2000 + 600 * (int)lut_apply(lut_field, (uint16_t)x) / FULL);
}

static void collect(rig_t *r)
{
    uint8_t wire[FRAME_MAX_WIRE];
    size_t w;

    for (uint8_t p = 0; p < TILE_PORTS; p++) {
        while ((w = tile_tx(&r->t, p, wire)) != 0) {
            frame_rx_t rx;
            sent_t s;
            frame_result_t res = FRAME_NONE;

            frame_rx_reset(&rx);
            for (size_t i = 0; i < w; i++)
                res = frame_rx_byte(&rx, wire[i], &s.type, s.pl, &s.len);
            CHECK_EQ(res, FRAME_OK);
            if (p == PORT_E && s.type != FT_HELLO && r->nout < 256)
                r->out[r->nout++] = s;
        }
    }
}

static void inject(rig_t *r, uint8_t type, const uint8_t *pl, uint8_t len)
{
    uint8_t wire[FRAME_MAX_WIRE];
    size_t w = frame_encode(type, pl, len, wire);

    tile_rx(&r->t, PORT_E, wire, w, r->now);
}

/* One millisecond: two scans, a tick, and the hub's HELLO and BEACON when
 * they are due. */
static void ms(rig_t *r)
{
    uint16_t adc[KEYS_N];
    uint8_t pl[BEACON_LEN];

    r->now++;
    if (r->hub_there && r->now % BEACON_PERIOD_MS == 0) {
        put32(pl, HUB);
        put16(pl + 4, ++r->seq);
        pl[6] = 0;
        pl[7] = 0;
        inject(r, FT_BEACON, pl, BEACON_LEN);
    }
    if (r->hub_there && r->now % HELLO_PERIOD_MS == 1) {
        link_hello_payload(pl, KIND_HUB, HUB, 0);
        inject(r, FT_HELLO, pl, HELLO_LEN);
    }
    for (int s = 0; s < 2; s++) {
        for (int i = 0; i < KEYS_N; i++)
            adc[i] = adc_at(r->x[i]);
        tile_scan(&r->t, ++r->scan, adc, r->now);
    }
    tile_tick(&r->t, r->now);
    collect(r);
}

static void run(rig_t *r, uint32_t n)
{
    while (n--)
        ms(r);
}

/* Move a key in a straight line over `n` milliseconds. */
static void move(rig_t *r, int key, int to, uint32_t n)
{
    int from = r->x[key];

    for (uint32_t i = 1; i <= n; i++) {
        r->x[key] = from + (int)((int64_t)(to - from) * i / n);
        ms(r);
    }
}

static void rig_init(rig_t *r, const uint8_t *record, size_t len)
{
    memset(r, 0, sizeof *r);
    r->now = 1000;
    tile_init(&r->t, UID, record, len, HZ, r->now);
    r->hub_there = true;
    run(r, 200);                /* warmed up, parent found */
    r->nout = 0;
}

/* The frames of one type among those sent. */
static int count(const rig_t *r, uint8_t type)
{
    int n = 0;

    for (int i = 0; i < r->nout; i++)
        n += r->out[i].type == type;
    return n;
}

static const sent_t *last(const rig_t *r, uint8_t type)
{
    for (int i = r->nout - 1; i >= 0; i--) {
        if (r->out[i].type == type)
            return &r->out[i];
    }
    return NULL;
}

static void cmd_to(rig_t *r, uint32_t target, uint8_t counter, uint8_t op,
                   const uint8_t *arg, uint8_t n)
{
    uint8_t pl[CMD_HEAD_LEN + 8];

    put32(pl, target);
    pl[4] = counter;
    pl[5] = op;
    if (n)
        memcpy(pl + CMD_HEAD_LEN, arg, n);
    inject(r, FT_CMD, pl, (uint8_t)(CMD_HEAD_LEN + n));
    collect(r);
}

static void cmd(rig_t *r, uint8_t counter, uint8_t op, const uint8_t *arg, uint8_t n)
{
    cmd_to(r, r->t.node.id, counter, op, arg, n);
}

/* Send a command and check the one reply: its counter, status and data length. */
static const sent_t *cmd_reply(rig_t *r, uint8_t counter, uint8_t op, const uint8_t *arg,
                               uint8_t n, uint8_t status, uint8_t data_len)
{
    static sent_t none;
    const sent_t *s;

    r->nout = 0;
    cmd(r, counter, op, arg, n);
    CHECK_EQ(count(r, FT_REPLY), 1);
    s = last(r, FT_REPLY);
    if (s == NULL)
        return &none;
    CHECK_EQ(get32(s->pl), r->t.node.id);
    CHECK_EQ(s->pl[4], counter);
    CHECK_EQ(s->pl[5], status);
    CHECK_EQ(s->len, UP_HEAD_LEN + 2 + data_len);
    return s;
}

/* ---- tests ---- */

static void records(void)
{
    tile_record_t a, b;
    hub_settings_t h = {48, 4, 7, 3, 1, 100, 12000, -20, 15}, g;
    uint8_t img[STORE_TILE_LEN], himg[STORE_HUB_LEN];

    CHECK_EQ(STORE_TILE_LEN, 204);
    memset(&a, 0, sizeof a);
    keys_default_params(a.param);
    a.param[PARAM_SWING] = -777;
    for (int i = 0; i < KEYS_N; i++)
        a.cal[i] = (key_cal_t){(uint16_t)(1000 + i), (uint16_t)(3000 - i)};
    store_tile_pack(&a, img);
    memset(&b, 0xEE, sizeof b);
    CHECK(store_tile_unpack(img, sizeof img, &b));
    CHECK(memcmp(a.param, b.param, sizeof a.param) == 0);
    CHECK(memcmp(a.cal, b.cal, sizeof a.cal) == 0);
    CHECK(store_tile_unpack(img, sizeof img + 100, &b));    /* a sector is longer than a record */

    /* anything wrong with it and it is no record */
    CHECK(!store_tile_unpack(img, sizeof img - 1, &b));
    CHECK(!store_tile_unpack(NULL, 0, &b));
    for (size_t bit = 0; bit < 8 * sizeof img; bit++) {
        img[bit / 8] ^= (uint8_t)(1u << (bit % 8));
        CHECK(!store_tile_unpack(img, sizeof img, &b));
        img[bit / 8] ^= (uint8_t)(1u << (bit % 8));
    }
    uint8_t blank[STORE_TILE_LEN];
    memset(blank, 0xFF, sizeof blank);                      /* erased flash */
    CHECK(!store_tile_unpack(blank, sizeof blank, &b));

    store_hub_pack(&h, himg);
    CHECK(store_hub_unpack(himg, sizeof himg, &g));
    CHECK_EQ(g.base, 48);
    CHECK_EQ(g.step_a, 4);
    CHECK_EQ(g.step_b, 7);
    CHECK_EQ(g.channel, 3);
    CHECK_EQ(g.aftertouch, 1);
    CHECK_EQ(g.vel1, 100);
    CHECK_EQ(g.vel127, 12000);
    CHECK_EQ(g.bend, -20);
    CHECK_EQ(g.pd_volts, 15);
    /* one kind of record is not the other */
    CHECK(!store_hub_unpack(img, sizeof img, &g));
    CHECK(!store_tile_unpack(himg, sizeof himg, &b));
    himg[9] ^= 1;
    CHECK(!store_hub_unpack(himg, sizeof himg, &g));
}

static void info(void)
{
    rig_t r;
    const sent_t *s;

    memset(&r, 0, sizeof r);
    r.now = 5000;
    tile_init(&r.t, UID, NULL, 0, HZ, r.now);
    CHECK_EQ(r.t.node.id, id_from_uid(UID));

    /* alone: nothing to send to anybody */
    run(&r, 1500);
    CHECK_EQ(r.nout, 0);

    /* the hub appears: INFO at once, saying who is where */
    r.hub_there = true;
    run(&r, 60);
    CHECK(count(&r, FT_INFO) >= 1);
    s = last(&r, FT_INFO);
    CHECK(s != NULL);
    if (s) {
        CHECK_EQ(s->len, INFO_LEN);
        CHECK_EQ(get32(s->pl), r.t.node.id);
        CHECK_EQ(get16(s->pl + 4), FW_VERSION);
        CHECK_EQ(s->pl[6], 0);                              /* flags */
        CHECK_EQ(s->pl[7], 8);
        CHECK_EQ(s->pl[8], 5);
        CHECK_EQ(s->pl[9], PORT_E);
        CHECK_EQ(s->pl[10], 0);                             /* the hub itself is the parent */
        CHECK_EQ(get32(s->pl + 11 + 5 * PORT_W), 0);
        CHECK_EQ(get32(s->pl + 11 + 5 * PORT_E), HUB);
        CHECK_EQ(s->pl[11 + 5 * PORT_E + 4], 0);
        CHECK_EQ(get16(s->pl + 31), 1);                     /* seconds since start */
        CHECK_EQ(get16(s->pl + 33), 0);
        CHECK_EQ(get16(s->pl + 35), 0);
    }

    /* then once a second */
    r.nout = 0;
    run(&r, 5000);
    CHECK_EQ(count(&r, FT_INFO), 5);
    CHECK_EQ(count(&r, FT_STATE), 50);
    s = last(&r, FT_INFO);
    if (s)
        CHECK_NEAR(get16(s->pl + 31), 6, 1);

    /* the hub goes: silence again after the parent times out */
    r.hub_there = false;
    run(&r, PARENT_TIMEOUT_MS + 5);
    r.nout = 0;
    run(&r, 2000);
    CHECK_EQ(r.nout, 0);
}

static void notes_and_state(void)
{
    rig_t r;
    const sent_t *s;

    rig_init(&r, NULL, 0);
    move(&r, 17, FULL, 50);
    CHECK_EQ(count(&r, FT_NOTE), 1);
    s = last(&r, FT_NOTE);
    if (s) {
        CHECK_EQ(s->len, NOTE_LEN);
        CHECK_EQ(get32(s->pl), r.t.node.id);
        CHECK_EQ(s->pl[4], 1);                              /* the first event */
        CHECK_EQ(s->pl[5], 0x80 | 17);
        CHECK_NEAR(get16(s->pl + 6), 2000, 80);
    }
    move(&r, 3, FULL, 100);
    run(&r, 150);
    s = last(&r, FT_STATE);
    CHECK(s != NULL);
    if (s) {
        CHECK_EQ(s->len, STATE_LEN);
        CHECK_EQ(s->pl[4], 2);                              /* two events so far */
        CHECK_EQ(s->pl[5], 1u << 3);
        CHECK_EQ(s->pl[6], 0);
        CHECK_EQ(s->pl[7], 1u << 1);                        /* key 17 */
        CHECK_EQ(s->pl[8], 0);
        CHECK_EQ(s->pl[9], 0);
    }
    r.nout = 0;
    move(&r, 17, 0, 25);
    CHECK_EQ(count(&r, FT_NOTE), 1);
    s = last(&r, FT_NOTE);
    if (s) {
        CHECK_EQ(s->pl[4], 3);
        CHECK_EQ(s->pl[5], 17);
        CHECK_NEAR(get16(s->pl + 6), 4000, 250);
    }

    /* with no hub the events are still counted, so the hub can tell */
    r.hub_there = false;
    run(&r, PARENT_TIMEOUT_MS + 5);
    r.nout = 0;
    move(&r, 3, 0, 50);
    CHECK_EQ(r.nout, 0);
    CHECK_EQ(r.t.events, 4);
}

static void commands(void)
{
    rig_t r;
    const sent_t *s;
    uint8_t arg[8];

    rig_init(&r, NULL, 0);

    cmd_reply(&r, 1, OP_PING, NULL, 0, ST_OK, 0);

    /* GET every parameter */
    static const int32_t want[] = {0, 150, 600, 500, 100, 0, 5, 20, 600};
    for (uint8_t p = 1; p <= TILE_PARAMS; p++) {
        arg[0] = p;
        s = cmd_reply(&r, (uint8_t)(10 + p), OP_GET, arg, 1, ST_OK, 4);
        CHECK_EQ((int32_t)get32(s->pl + 6), want[p]);
    }
    arg[0] = 0;
    cmd_reply(&r, 30, OP_GET, arg, 1, ST_UNKNOWN, 0);
    arg[0] = TILE_PARAMS + 1;
    cmd_reply(&r, 31, OP_GET, arg, 1, ST_UNKNOWN, 0);
    cmd_reply(&r, 32, OP_GET, arg, 0, ST_BAD_ARG, 0);

    /* SET: good, out of range, negative, short, unknown */
    arg[0] = PARAM_HI;
    put32(arg + 1, 700);
    cmd_reply(&r, 40, OP_SET, arg, 5, ST_OK, 0);
    CHECK_EQ(r.t.keys.param[PARAM_HI], 700);
    put32(arg + 1, 100);
    cmd_reply(&r, 41, OP_SET, arg, 5, ST_BAD_ARG, 0);
    CHECK_EQ(r.t.keys.param[PARAM_HI], 700);
    arg[0] = PARAM_SWING;
    put32(arg + 1, (uint32_t)-500);
    cmd_reply(&r, 42, OP_SET, arg, 5, ST_OK, 0);
    CHECK_EQ(r.t.keys.param[PARAM_SWING], -500);
    cmd_reply(&r, 43, OP_SET, arg, 4, ST_BAD_ARG, 0);
    arg[0] = 99;
    cmd_reply(&r, 44, OP_SET, arg, 5, ST_UNKNOWN, 0);

    cmd_reply(&r, 50, 0x77, NULL, 0, ST_UNKNOWN, 0);
    arg[0] = 3;
    cmd_reply(&r, 51, OP_CAL, arg, 1, ST_BAD_ARG, 0);
    cmd_reply(&r, 52, OP_CAL, arg, 0, ST_BAD_ARG, 0);
    arg[0] = 2;
    cmd_reply(&r, 53, OP_RAW, arg, 1, ST_BAD_ARG, 0);
    cmd_reply(&r, 54, OP_QUIET, arg, 1, ST_BAD_ARG, 0);

    /* a command for another tile is not run and not answered */
    r.nout = 0;
    cmd_to(&r, 0x12345678u, 60, OP_PING, NULL, 0);
    CHECK_EQ(count(&r, FT_REPLY), 0);
}

/* The reply was lost and the hub asks again: the same answer, nothing done twice. */
static void repeats(void)
{
    rig_t r;
    uint8_t arg[2];

    rig_init(&r, NULL, 0);
    arg[0] = CAL_START;
    cmd_reply(&r, 7, OP_CAL, arg, 1, ST_OK, 0);
    CHECK(r.t.keys.calibrating);
    keys_cal(&r.t.keys, CAL_STOP);                          /* behind its back */
    cmd_reply(&r, 7, OP_CAL, arg, 1, ST_OK, 0);
    CHECK(!r.t.keys.calibrating);                           /* not started again */
    cmd_reply(&r, 8, OP_CAL, arg, 1, ST_OK, 0);             /* a new counter is a new command */
    CHECK(r.t.keys.calibrating);

    /* CAL stop says how many keys were measured */
    move(&r, 0, FULL, 50);
    move(&r, 0, 0, 50);
    move(&r, 1, FULL, 50);
    move(&r, 1, 0, 50);
    arg[0] = CAL_STOP;
    const sent_t *s = cmd_reply(&r, 9, OP_CAL, arg, 1, ST_OK, 1);
    CHECK_EQ(s->pl[6], 2);

    /* for everybody: no reply, run once however often it comes */
    r.nout = 0;
    arg[0] = 1;
    for (int i = 0; i < BCAST_REPEATS; i++) {
        cmd_to(&r, ID_ALL, 20, OP_RAW, arg, 1);
        CHECK(r.t.raw || i > 0);
        r.t.raw = false;
    }
    CHECK(!r.t.raw);
    CHECK_EQ(count(&r, FT_REPLY), 0);
    /* its counter is its own: the same number aimed at the tile is new */
    cmd_reply(&r, 20, OP_RAW, arg, 1, ST_OK, 0);
    CHECK(r.t.raw);
    r.t.raw = false;
    cmd_to(&r, ID_ALL, 21, OP_RAW, arg, 1);
    CHECK(r.t.raw);
}

/* A hub gone long enough to be forgotten: its counters are forgotten with it. */
static void counters_forgotten(void)
{
    rig_t r;
    uint8_t arg[1] = {1};

    rig_init(&r, NULL, 0);
    cmd_reply(&r, 5, OP_RAW, arg, 1, ST_OK, 0);
    r.t.raw = false;

    r.hub_there = false;
    run(&r, PARENT_TIMEOUT_MS + 100);
    r.hub_there = true;
    run(&r, 100);
    cmd_reply(&r, 5, OP_RAW, arg, 1, ST_OK, 0);             /* remembered: a repeat */
    CHECK(!r.t.raw);

    r.hub_there = false;
    run(&r, PARENT_TIMEOUT_MS + SEQ_FORGET_MS + 10);
    r.hub_there = true;
    r.seq = 0;                                              /* a hub that started again */
    run(&r, 100);
    CHECK_EQ(r.t.node.parent, PORT_E);
    cmd_reply(&r, 5, OP_RAW, arg, 1, ST_OK, 0);
    CHECK(r.t.raw);
}

static void save(void)
{
    rig_t r, again;
    uint8_t img[STORE_TILE_LEN], arg[5];

    rig_init(&r, NULL, 0);
    arg[0] = PARAM_LO;
    put32(arg + 1, 222);
    cmd(&r, 1, OP_SET, arg, 5);
    arg[0] = CAL_START;
    cmd(&r, 2, OP_CAL, arg, 1);
    for (int i = 0; i < KEYS_N; i++) {
        move(&r, i, FULL, 10);
        run(&r, 5);                                         /* a finger rests on the floor a moment */
        move(&r, i, 0, 10);
    }
    arg[0] = CAL_STOP;
    cmd(&r, 3, OP_CAL, arg, 1);
    CHECK_EQ(keys_measured(&r.t.keys), KEYS_N);
    CHECK_EQ(tile_actions(&r.t), 0);

    /* SAVE: the port is asked to write; the reply waits for it */
    r.nout = 0;
    cmd(&r, 4, OP_SAVE, NULL, 0);
    CHECK_EQ(count(&r, FT_REPLY), 0);
    CHECK_EQ(tile_actions(&r.t), TILE_ACT_SAVE);
    CHECK_EQ(tile_actions(&r.t), 0);                        /* asked once */
    cmd(&r, 4, OP_SAVE, NULL, 0);                           /* the hub asks again meanwhile */
    CHECK_EQ(count(&r, FT_REPLY), 0);
    CHECK_EQ(tile_actions(&r.t), 0);
    cmd_reply(&r, 5, OP_SAVE, NULL, 0, ST_BUSY, 0);         /* another one: not now */
    tile_save_image(&r.t, img);
    r.nout = 0;
    tile_save_done(&r.t, true);
    collect(&r);
    CHECK_EQ(count(&r, FT_REPLY), 1);
    CHECK_EQ(last(&r, FT_REPLY)->pl[4], 4);
    CHECK_EQ(last(&r, FT_REPLY)->pl[5], ST_OK);
    tile_save_done(&r.t, true);                             /* twice does nothing */
    collect(&r);
    CHECK_EQ(count(&r, FT_REPLY), 1);

    /* a write that failed says so; and a repeat afterwards hears it again */
    r.nout = 0;
    cmd(&r, 6, OP_SAVE, NULL, 0);
    CHECK_EQ(tile_actions(&r.t), TILE_ACT_SAVE);
    tile_save_done(&r.t, false);
    collect(&r);
    CHECK_EQ(last(&r, FT_REPLY)->pl[5], ST_FAILED);
    cmd_reply(&r, 6, OP_SAVE, NULL, 0, ST_FAILED, 0);
    CHECK_EQ(tile_actions(&r.t), 0);

    /* for everybody: written, not answered */
    r.nout = 0;
    cmd_to(&r, ID_ALL, 9, OP_SAVE, NULL, 0);
    CHECK_EQ(tile_actions(&r.t), TILE_ACT_SAVE);
    tile_save_done(&r.t, true);
    collect(&r);
    CHECK_EQ(count(&r, FT_REPLY), 0);

    /* the record, given to a tile that starts, brings it all back */
    memset(&again, 0, sizeof again);
    again.now = 77;
    tile_init(&again.t, UID, img, sizeof img, HZ, again.now);
    CHECK_EQ(again.t.keys.param[PARAM_LO], 222);
    CHECK_EQ(keys_measured(&again.t.keys), KEYS_N);
    for (int i = 0; i < KEYS_N; i++) {
        CHECK_NEAR(again.t.keys.key[i].swing, (adc_at(FULL) - adc_at(0)) * 16, 16);
        CHECK(again.t.keys.key[i].measured);
    }
    again.hub_there = true;
    run(&again, 200);
    CHECK(last(&again, FT_INFO) != NULL);
    if (last(&again, FT_INFO))
        CHECK_EQ(last(&again, FT_INFO)->pl[6], INFO_CALIBRATED);

    /* a record that is damaged: defaults */
    img[20] ^= 4;
    tile_init(&again.t, UID, img, sizeof img, HZ, 0);
    CHECK_EQ(again.t.keys.param[PARAM_LO], 150);
    CHECK_EQ(keys_measured(&again.t.keys), 0);
}

static void reboot_and_bootloader(void)
{
    rig_t r;

    rig_init(&r, NULL, 0);
    cmd_reply(&r, 1, OP_REBOOT, NULL, 0, ST_OK, 0);
    for (uint32_t i = 0; i + 1 < ACTION_DELAY_MS; i++) {
        ms(&r);
        CHECK_EQ(tile_actions(&r.t), 0);
    }
    /* the hub did not hear and asks again: answered again, still one restart */
    cmd_reply(&r, 1, OP_REBOOT, NULL, 0, ST_OK, 0);
    ms(&r);
    CHECK_EQ(tile_actions(&r.t), TILE_ACT_REBOOT);
    run(&r, 500);
    CHECK_EQ(tile_actions(&r.t), 0);

    cmd_reply(&r, 2, OP_BOOTLOADER, NULL, 0, ST_OK, 0);
    run(&r, ACTION_DELAY_MS - 1);
    CHECK_EQ(tile_actions(&r.t), 0);
    ms(&r);
    CHECK_EQ(tile_actions(&r.t), TILE_ACT_BOOTLOADER);
}

static void quiet(void)
{
    rig_t r;
    uint8_t arg[2];

    rig_init(&r, NULL, 0);
    put16(arg, 400);
    cmd_reply(&r, 1, OP_QUIET, arg, 2, ST_OK, 0);           /* the reply still gets out */
    run(&r, QUIET_LEAD_MS);
    r.nout = 0;
    uint8_t wire[FRAME_MAX_WIRE];
    for (int i = 0; i < 400 - 1; i++) {
        ms(&r);
        for (uint8_t p = 0; p < TILE_PORTS; p++)
            CHECK_EQ(tile_tx(&r.t, p, wire), 0);
    }
    CHECK_EQ(r.nout, 0);
    run(&r, 200);
    CHECK(count(&r, FT_STATE) >= 1);                        /* back */
    CHECK(count(&r, FT_INFO) >= 1);

    /* a bootloader restart ordered before the quiet still happens in it */
    cmd(&r, 2, OP_BOOTLOADER, NULL, 0);
    put16(arg, 5000);
    cmd_to(&r, ID_ALL, 3, OP_QUIET, arg, 2);
    unsigned acts = 0;
    for (uint32_t i = 0; i < ACTION_DELAY_MS + 5; i++) {
        ms(&r);
        acts |= tile_actions(&r.t);
    }
    CHECK_EQ(acts, TILE_ACT_BOOTLOADER);
}

static void positions(void)
{
    rig_t r;
    const sent_t *s;
    uint8_t arg[5];

    rig_init(&r, NULL, 0);
    move(&r, 4, FULL, 50);
    run(&r, 100);
    CHECK_EQ(count(&r, FT_POS), 0);                         /* mode 0: never */

    /* mode 1: how far past `hi` the keys that are on are */
    arg[0] = PARAM_POS_MODE;
    put32(arg + 1, 1);
    cmd(&r, 1, OP_SET, arg, 5);
    r.nout = 0;
    run(&r, 20);
    CHECK_EQ(count(&r, FT_POS), 1);                         /* told once ... */
    s = last(&r, FT_POS);
    if (s) {
        CHECK_EQ(s->len, UP_HEAD_LEN + 2);
        CHECK_EQ(s->pl[4], 4);
        CHECK_EQ(s->pl[5], 255);
    }
    run(&r, 200);
    CHECK_EQ(count(&r, FT_POS), 1);                         /* ... and not again while nothing moves */

    r.nout = 0;
    move(&r, 4, FULL * 8 / 10, 100);
    CHECK(count(&r, FT_POS) > 5);
    CHECK(count(&r, FT_POS) <= 100 / 5 + 1);                /* every 5 ms at most */
    run(&r, 10);                                            /* where it stopped is told too */
    s = last(&r, FT_POS);
    if (s)
        CHECK_NEAR(s->pl[5], 127, 4);
    r.nout = 0;
    move(&r, 4, 0, 50);
    run(&r, 20);
    s = last(&r, FT_POS);
    if (s)
        CHECK_EQ(s->pl[5], 0);

    /* mode 2, every key moving: more than fit one frame; all are told in turn */
    put32(arg + 1, 2);
    cmd(&r, 2, OP_SET, arg, 5);
    r.nout = 0;
    for (int step = 1; step <= 40; step++) {
        for (int i = 0; i < KEYS_N; i++)
            r.x[i] = FULL / 80 * step;
        ms(&r);
    }
    run(&r, 30);
    CHECK(count(&r, FT_POS) >= 2);
    bool seen[KEYS_N] = {false};
    for (int i = 0; i < r.nout; i++) {
        if (r.out[i].type != FT_POS)
            continue;
        CHECK(r.out[i].len <= UP_HEAD_LEN + 2 * POS_MAX_PAIRS);
        for (int j = UP_HEAD_LEN; j + 1 < r.out[i].len; j += 2) {
            CHECK(r.out[i].pl[j] < KEYS_N);
            seen[r.out[i].pl[j]] = true;
        }
    }
    for (int i = 0; i < KEYS_N; i++) {
        CHECK(seen[i]);
        CHECK_NEAR(r.t.depth_sent[i], 127, 3);              /* and the hub's picture is current */
    }
}

static void raw(void)
{
    rig_t r;
    uint8_t arg[5];
    int frames = 0;

    rig_init(&r, NULL, 0);
    r.x[25] = FULL;
    arg[0] = 1;
    cmd(&r, 1, OP_RAW, arg, 1);
    r.nout = 0;
    run(&r, 200);
    CHECK_NEAR(count(&r, FT_RAW), 2 * 200 / 20, 2);         /* two frames every 20 ms */
    for (int i = 0; i < r.nout; i++) {
        const sent_t *s = &r.out[i];
        if (s->type != FT_RAW)
            continue;
        CHECK_EQ(s->len, UP_HEAD_LEN + 3 + 40);
        CHECK_EQ(s->pl[5], frames % 2 ? 20 : 0);            /* first key */
        CHECK_EQ(s->pl[6], 20);
        if (frames % 2) {
            CHECK_EQ(s->pl[4], r.out[i - 1].pl[4]);         /* both halves of one scan */
            CHECK_EQ(get16(s->pl + 7 + 2 * 5), adc_at(FULL));   /* key 25, on the floor */
            CHECK_EQ(get16(s->pl + 7), adc_at(0));
        }
        frames++;
    }
    CHECK(last(&r, FT_INFO) != NULL);
    if (last(&r, FT_INFO))
        CHECK_EQ(last(&r, FT_INFO)->pl[6] & INFO_RAW, INFO_RAW);

    arg[0] = 0;
    cmd(&r, 2, OP_RAW, arg, 1);
    r.nout = 0;
    run(&r, 100);
    CHECK_EQ(count(&r, FT_RAW), 0);
}

int main(void)
{
    RUN(records);
    RUN(info);
    RUN(notes_and_state);
    RUN(commands);
    RUN(repeats);
    RUN(counters_forgotten);
    RUN(save);
    RUN(reboot_and_bootloader);
    RUN(quiet);
    RUN(positions);
    RUN(raw);
    return T_DONE();
}
