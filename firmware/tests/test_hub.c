/* hub.c, grid.c and console.c: a real hub, and tiles played by hand.
 *
 * The fake tiles send what real ones send (HELLO, INFO, STATE, NOTE) and
 * answer commands the way tile.c does: a repeat of the last command gets
 * the reply again and is not run twice. */
#include "hub.h"
#include "t.h"

#include <stdio.h>

static const uint8_t UID[12] = {9, 8, 7, 6, 5, 4, 3, 2, 1, 0, 1, 2};

#define MAX_FAKES 40

typedef struct {
    uint32_t id;
    bool alive;                 /* sends STATE and INFO by itself */
    bool root;                  /* on the hub's cable: sends HELLO */
    int deaf;                   /* commands it will still not hear */
    int mute;                   /* replies that will still get lost */
    uint8_t status;             /* what it answers */

    uint8_t events;
    uint8_t bitmap[5];
    uint8_t flags, cols, rows, parent, hops;
    uint32_t nbr_id[TILE_PORTS];
    uint8_t nbr_port[TILE_PORTS];

    int heard;                  /* commands for it that came */
    int runs;                   /* ... and were new, so it ran them */
    int bcast_runs;
    bool have_cmd, have_bcast;
    uint8_t last_cmd, last_bcast;
    uint8_t op, arg[8], arg_len;        /* the last command it ran */
    uint8_t reply[6], reply_len;
} fake_t;

typedef struct {
    uint32_t at, target;
    uint8_t counter, op, arg[8], arg_len;
} cmd_t;

static hub_t h;
static uint32_t now;
static fake_t fake[MAX_FAKES];
static int nfake;

static cmd_t cmds[8192];
static int ncmds;
static uint8_t midi[8192][3];
static int nmidi;
static char text[1 << 17];
static size_t ntext;
static int beacons, frames;
static uint32_t last_frame;
static unsigned acts;
static uint32_t act_at;
static bool hold_midi, hold_text;

static void clear(void)
{
    ncmds = nmidi = beacons = frames = 0;
    ntext = 0;
    text[0] = 0;
    acts = 0;
}

/* ---- what the fakes send ---- */

static void up(uint32_t id, uint8_t type, const uint8_t *body, uint8_t n)
{
    uint8_t pl[FRAME_MAX_PAYLOAD], wire[FRAME_MAX_WIRE];

    put32(pl, id);
    if (n)
        memcpy(pl + UP_HEAD_LEN, body, n);
    size_t w = frame_encode(type, pl, (size_t)n + UP_HEAD_LEN, wire);
    hub_rx(&h, wire, w, now);
}

static void send_hello(int i)
{
    uint8_t pl[HELLO_LEN], wire[FRAME_MAX_WIRE];

    link_hello_payload(pl, KIND_TILE, fake[i].id, PORT_E);
    size_t w = frame_encode(FT_HELLO, pl, HELLO_LEN, wire);
    hub_rx(&h, wire, w, now);
}

static void send_info(int i)
{
    const fake_t *f = &fake[i];
    uint8_t b[INFO_LEN - UP_HEAD_LEN] = {0};

    put16(b, FW_VERSION);
    b[2] = f->flags;
    b[3] = f->cols;
    b[4] = f->rows;
    b[5] = f->parent;
    b[6] = f->hops;
    for (int p = 0; p < TILE_PORTS; p++) {
        put32(b + 7 + 5 * p, f->nbr_id[p]);
        b[7 + 5 * p + 4] = f->nbr_port[p];
    }
    put16(b + 27, 12);
    put16(b + 29, 3);
    put16(b + 31, 4);
    up(f->id, FT_INFO, b, sizeof b);
}

static void state_raw(int i, uint8_t counter, const uint8_t bitmap[5])
{
    uint8_t b[STATE_LEN - UP_HEAD_LEN];

    b[0] = counter;
    memcpy(b + 1, bitmap, 5);
    up(fake[i].id, FT_STATE, b, sizeof b);
}

static void send_state(int i)
{
    state_raw(i, fake[i].events, fake[i].bitmap);
}

static void note_raw(int i, uint8_t counter, uint8_t key, bool on, uint16_t speed)
{
    uint8_t b[NOTE_LEN - UP_HEAD_LEN];

    b[0] = counter;
    b[1] = (uint8_t)(key | (on ? 0x80u : 0u));
    put16(b + 2, speed);
    up(fake[i].id, FT_NOTE, b, sizeof b);
}

/* The key moves on the fake; the NOTE frame may or may not get through. */
static void key_moves(int i, uint8_t key, bool on)
{
    fake_t *f = &fake[i];

    f->events++;
    if (on)
        f->bitmap[key >> 3] |= (uint8_t)(1u << (key & 7));
    else
        f->bitmap[key >> 3] &= (uint8_t)~(1u << (key & 7));
}

static void note(int i, uint8_t key, bool on, uint16_t speed)
{
    key_moves(i, key, on);
    note_raw(i, fake[i].events, key, on, speed);
}

/* ---- what the fakes do with commands ---- */

static void fake_cmd(int i, const cmd_t *c)
{
    fake_t *f = &fake[i];

    if (c->target == ID_ALL) {
        if (f->have_bcast && f->last_bcast == c->counter)
            return;
        f->have_bcast = true;
        f->last_bcast = c->counter;
        f->bcast_runs++;
        f->op = c->op;
        memcpy(f->arg, c->arg, sizeof f->arg);
        f->arg_len = c->arg_len;
        return;
    }

    f->heard++;
    if (f->deaf > 0) {
        f->deaf--;
        return;
    }
    if (!(f->have_cmd && f->last_cmd == c->counter)) {
        f->have_cmd = true;
        f->last_cmd = c->counter;
        f->runs++;
        f->op = c->op;
        memcpy(f->arg, c->arg, sizeof f->arg);
        f->arg_len = c->arg_len;

        f->reply[0] = c->counter;
        f->reply[1] = f->status;
        f->reply_len = 2;
        if (f->status == ST_OK && c->op == OP_GET) {
            put32(f->reply + 2, 100u + c->arg[0]);
            f->reply_len = 6;
        } else if (f->status == ST_OK && c->op == OP_CAL && c->arg[0] == CAL_STOP) {
            f->reply[2] = 37;
            f->reply_len = 3;
        }
    }
    if (f->mute > 0) {
        f->mute--;
        return;
    }
    up(f->id, FT_REPLY, f->reply, f->reply_len);
}

/* ---- the rig ---- */

/* Take everything the hub has to give: frames for the cable, MIDI, text. */
static void drain(void)
{
    uint8_t wire[FRAME_MAX_WIRE], pl[FRAME_MAX_PAYLOAD], type = 0, len = 0, m[3], buf[256];
    size_t w, n;

    while ((w = hub_tx(&h, wire)) != 0) {
        frame_rx_t rx;
        frame_result_t res = FRAME_NONE;

        frame_rx_reset(&rx);
        for (size_t i = 0; i < w; i++)
            res = frame_rx_byte(&rx, wire[i], &type, pl, &len);
        CHECK_EQ(res, FRAME_OK);
        frames++;
        last_frame = now;
        if (type == FT_BEACON)
            beacons++;
        if (type != FT_CMD || len < CMD_HEAD_LEN || ncmds >= 8192)
            continue;

        cmd_t *c = &cmds[ncmds++];
        memset(c, 0, sizeof *c);
        c->at = now;
        c->target = get32(pl);
        c->counter = pl[4];
        c->op = pl[5];
        c->arg_len = (uint8_t)(len - CMD_HEAD_LEN);
        memcpy(c->arg, pl + CMD_HEAD_LEN, c->arg_len > 8 ? 8 : c->arg_len);
        for (int i = 0; i < nfake; i++) {
            if (fake[i].alive && (c->target == ID_ALL || c->target == fake[i].id))
                fake_cmd(i, c);
        }
    }
    while (!hold_midi && nmidi < 8192 && hub_midi_get(&h, m))
        memcpy(midi[nmidi++], m, 3);
    while (!hold_text && (n = hub_console_read(&h, buf, sizeof buf)) != 0) {
        if (ntext + n < sizeof text) {
            memcpy(text + ntext, buf, n);
            ntext += n;
            text[ntext] = 0;
        }
    }
    unsigned a = hub_actions(&h);
    if (a) {
        acts |= a;
        act_at = now;
    }
}

static void step(void)
{
    now++;
    for (int i = 0; i < nfake; i++) {
        if (!fake[i].alive)
            continue;
        if (fake[i].root && now % HELLO_PERIOD_MS == 0)
            send_hello(i);
        if ((now + 7u * (unsigned)i) % STATE_PERIOD_MS == 3)
            send_state(i);
        if ((now + 13u * (unsigned)i) % INFO_PERIOD_MS == 500)
            send_info(i);
    }
    hub_tick(&h, now);
    drain();
}

static void run(uint32_t ms)
{
    while (ms--)
        step();
}

/* Fake i's port pi is cabled to fake j's port pj. */
static void cable(int i, uint8_t pi, int j, uint8_t pj)
{
    fake[i].nbr_id[pi] = fake[j].id;
    fake[i].nbr_port[pi] = pj;
    fake[j].nbr_id[pj] = fake[i].id;
    fake[j].nbr_port[pj] = pi;
}

/* The hub's cable goes to fake i's east port. */
static void set_root(int i)
{
    for (int k = 0; k < nfake; k++) {
        if (fake[k].root) {
            fake[k].root = false;
            fake[k].nbr_id[PORT_E] = ID_NONE;
        }
    }
    fake[i].root = true;
    fake[i].nbr_id[PORT_E] = h.node.id;
    fake[i].nbr_port[PORT_E] = 0;
    fake[i].parent = PORT_E;
    fake[i].hops = 0;
}

/* A hub that just started and n tiles nobody has cabled together yet. */
static void start(int n)
{
    memset(fake, 0, sizeof fake);
    nfake = n;
    hold_midi = hold_text = false;
    now = 1000;
    hub_init(&h, UID, NULL, 0, now);
    for (int i = 0; i < n; i++) {
        fake_t *f = &fake[i];
        /* IDs in no particular order */
        f->id = 0x51000000u + (uint32_t)((i * 37 + 11) % 64) * 0x101u;
        f->alive = true;
        f->cols = KEYS_COLS;
        f->rows = KEYS_ROWS;
        f->parent = PORT_NONE;
        f->hops = 0xFF;
    }
    if (n)
        set_root(0);
    clear();
}

/* Everybody says who they are; the hub lays them out. Table index = fake index. */
static void settle(void)
{
    for (int i = 0; i < nfake; i++) {
        if (!fake[i].alive)
            continue;
        if (fake[i].root)
            send_hello(i);
        send_info(i);
        send_state(i);
    }
    run(3);
    clear();
}

static hub_tile_t *T(int i)
{
    return hub_find(&h, fake[i].id);
}

/* Type a line. Everything has to be taken at once. */
static void type(const char *line)
{
    size_t n = strlen(line);

    drain();
    clear();
    CHECK_EQ(hub_console_rx(&h, (const uint8_t *)line, n, now), n);
    CHECK_EQ(hub_console_rx(&h, (const uint8_t *)"\n", 1, now), 1);
    drain();
}

static void wait_done(void)
{
    for (int i = 0; i < 10000 && (h.job.kind != JOB_NONE || h.bcast_left); i++)
        step();
    step();
}

/* Type a line and wait for its `ok` or `error`. */
static void say(const char *line)
{
    type(line);
    wait_done();
}

static bool said(const char *what)
{
    return strstr(text, what) != NULL;
}

static int times_said(const char *what)
{
    int n = 0;

    for (const char *p = text; (p = strstr(p, what)) != NULL; p += strlen(what))
        n++;
    return n;
}

/* The text ends with this line. */
static bool ends(const char *line)
{
    size_t n = strlen(line);

    return ntext >= n + 2 && !memcmp(text + ntext - n - 2, line, n) &&
           !memcmp(text + ntext - 2, "\r\n", 2);
}

#define CHECK_MIDI(i, s, a, b) do { \
        CHECK((i) < nmidi); \
        if ((i) < nmidi) { \
            CHECK_EQ(midi[i][0], s); \
            CHECK_EQ(midi[i][1], a); \
            CHECK_EQ(midi[i][2], b); \
        } \
    } while (0)

static int used(void)
{
    int n = 0;

    for (int i = 0; i < HUB_MAX_TILES; i++)
        n += h.tile[i].used;
    return n;
}

static int placed(void)
{
    int n = 0;

    for (int i = 0; i < HUB_MAX_TILES; i++)
        n += h.tile[i].used && h.tile[i].placed;
    return n;
}

/* ------------------------------------------------------------- the table --- */

static void table(void)
{
    uint8_t none[5] = {0};

    start(1);
    CHECK_EQ(used(), 0);

    /* a tile is known from its first frame, whatever that is */
    send_state(0);
    CHECK_EQ(used(), 1);
    CHECK(T(0) != NULL && T(0) == &h.tile[0]);
    CHECK(!T(0)->have_info);
    CHECK(!T(0)->placed);

    /* frames from nobody, everybody or the hub itself are not tiles */
    uint8_t b[STATE_LEN - UP_HEAD_LEN] = {0};
    up(ID_NONE, FT_STATE, b, sizeof b);
    up(ID_ALL, FT_STATE, b, sizeof b);
    up(h.node.id, FT_STATE, b, sizeof b);
    CHECK_EQ(used(), 1);

    settle();
    CHECK(T(0)->have_info);
    CHECK(T(0)->placed);
    CHECK_EQ(T(0)->version, FW_VERSION);
    CHECK_EQ(T(0)->uptime, 12);
    CHECK_EQ(T(0)->crc_drops, 3);
    CHECK_EQ(T(0)->queue_drops, 4);

    /* silent for 500 ms: its notes stop */
    note(0, 0, true, 1000);
    CHECK_EQ(nmidi, 0);
    drain();
    CHECK_EQ(nmidi, 1);
    fake[0].alive = false;
    uint32_t last = T(0)->last_heard;
    clear();
    while (now - last < TILE_SILENT_MS - 1)
        step();
    CHECK(!T(0)->silent);
    CHECK_EQ(nmidi, 0);
    step();
    CHECK(T(0)->silent);
    CHECK_EQ(h.stats.silent, 1);
    CHECK_EQ(nmidi, 1);
    CHECK_MIDI(0, 0x80, 48, 64);
    CHECK_EQ(h.held[48], 0);

    /* its link went down meanwhile, and with it every tile's place */
    CHECK(!T(0)->placed);
    send_hello(0);
    step();
    CHECK(T(0)->placed);

    /* heard again: not silent, and the key it still holds comes back */
    state_raw(0, fake[0].events, fake[0].bitmap);
    CHECK(!T(0)->silent);
    state_raw(0, fake[0].events, fake[0].bitmap);
    drain();
    CHECK_EQ(nmidi, 2);
    CHECK_MIDI(1, 0x90, 48, notes_velocity_mid(&h.set));
    state_raw(0, fake[0].events, none);
    state_raw(0, fake[0].events, none);

    /* not heard for 3 s: forgotten */
    last = T(0)->last_heard;
    while (now - last < TILE_FORGET_MS - 1)
        step();
    CHECK_EQ(used(), 1);
    step();
    CHECK_EQ(used(), 0);
    CHECK_EQ(h.stats.silent, 2);

    /* 32 tiles fit, the 33rd is counted and left out */
    start(0);
    for (uint32_t i = 0; i < HUB_MAX_TILES + 1; i++)
        up(0x100 + i, FT_STATE, b, sizeof b);
    CHECK_EQ(used(), HUB_MAX_TILES);
    CHECK_EQ(h.stats.no_room, 1);
    CHECK(hub_find(&h, 0x100 + HUB_MAX_TILES) == NULL);
    up(0x100 + HUB_MAX_TILES, FT_STATE, b, sizeof b);
    CHECK_EQ(h.stats.no_room, 2);

    /* short frames do no harm */
    start(1);
    settle();
    up(fake[0].id, FT_NOTE, b, 3);
    up(fake[0].id, FT_STATE, b, 5);
    up(fake[0].id, FT_INFO, b, 6);
    up(fake[0].id, FT_REPLY, b, 1);
    up(fake[0].id, FT_POS, b, 1);
    up(fake[0].id, FT_RAW, b, 2);
    up(fake[0].id, 0x2F, b, 6);
    drain();
    CHECK_EQ(nmidi, 0);
    CHECK_EQ(ntext, 0);
    CHECK(T(0)->placed);
}

/* --------------------------------------------------------------- the grid --- */

static void grid_line(void)
{
    start(3);
    cable(0, PORT_W, 1, PORT_E);
    cable(1, PORT_W, 2, PORT_E);
    settle();

    CHECK_EQ(hub_root(&h), fake[0].id);
    CHECK_EQ(placed(), 3);
    CHECK_EQ(T(0)->u, 2);
    CHECK_EQ(T(1)->u, 1);
    CHECK_EQ(T(2)->u, 0);
    CHECK_EQ(T(0)->v + T(1)->v + T(2)->v, 0);
    CHECK_EQ(h.width, 3);
    CHECK_EQ(h.height, 1);
    CHECK_EQ(h.nproblems, 0);

    /* a column, the hub's tile at the top */
    start(3);
    cable(0, PORT_S, 1, PORT_N);
    cable(1, PORT_S, 2, PORT_N);
    settle();
    CHECK_EQ(T(0)->v, 2);
    CHECK_EQ(T(1)->v, 1);
    CHECK_EQ(T(2)->v, 0);
    CHECK_EQ(h.width, 1);
    CHECK_EQ(h.height, 3);

    /* a tile that is not cabled to anything placed has no place */
    start(2);
    settle();
    CHECK(T(0)->placed);
    CHECK(!T(1)->placed);
    CHECK_EQ(h.width, 1);
    CHECK_EQ(h.height, 1);
    /* ... until its cable is in */
    cable(0, PORT_N, 1, PORT_S);
    send_info(0);
    send_info(1);
    run(2);
    CHECK(T(1)->placed);
    CHECK_EQ(T(1)->u, 0);
    CHECK_EQ(T(1)->v, 1);
    CHECK_EQ(h.height, 2);
    /* ... and out again */
    fake[0].nbr_id[PORT_N] = ID_NONE;
    fake[1].nbr_id[PORT_S] = ID_NONE;
    send_info(0);
    send_info(1);
    run(2);
    CHECK(!T(1)->placed);
    CHECK_EQ(h.height, 1);
}

/* Nine tiles in a square, every neighbour cabled: twelve cables, four loops. */
static void grid_3x3(void)
{
    start(9);
    for (int y = 0; y < 3; y++) {
        for (int x = 0; x < 3; x++) {
            if (x < 2)
                cable(x + 3 * y, PORT_E, x + 1 + 3 * y, PORT_W);
            if (y < 2)
                cable(x + 3 * y, PORT_N, x + 3 * (y + 1), PORT_S);
        }
    }
    set_root(5);                /* the right-hand tile of the middle row */
    settle();

    CHECK_EQ(hub_root(&h), fake[5].id);
    CHECK_EQ(placed(), 9);
    for (int i = 0; i < 9; i++) {
        CHECK_EQ(T(i)->u, i % 3);
        CHECK_EQ(T(i)->v, i / 3);
    }
    CHECK_EQ(h.width, 3);
    CHECK_EQ(h.height, 3);
    CHECK_EQ(h.nproblems, 0);

    say("grid");
    CHECK(said("hub on tile 5\r\n3 x 3\r\n  6  7  8\r\n  3  4  5\r\n  0  1  2\r\nok\r\n"));

    /* the hub's cable moves to the bottom left tile: same picture */
    set_root(0);
    for (int i = 0; i < 9; i++)
        send_info(i);
    run(150);
    CHECK_EQ(hub_root(&h), fake[0].id);
    for (int i = 0; i < 9; i++) {
        CHECK_EQ(T(i)->u, i % 3);
        CHECK_EQ(T(i)->v, i / 3);
    }
    say("grid");
    CHECK(said("hub on tile 0\r\n3 x 3\r\n"));
}

static void grid_problems(void)
{
    /* a north port cabled to a north port: no telling where that tile is */
    start(2);
    cable(0, PORT_N, 1, PORT_N);
    settle();
    CHECK(T(0)->placed);
    CHECK(!T(1)->placed);
    CHECK_EQ(h.nproblems, 1);
    CHECK_EQ(h.problem[0].what, PROBLEM_PORTS);
    CHECK(h.problem[0].a < h.problem[0].b);
    CHECK_EQ(h.problem[0].a_port, PORT_N);
    CHECK_EQ(h.problem[0].b_port, PORT_N);
    say("grid");
    CHECK(said("unplaced: 1\r\n"));
    CHECK(said(" port N is cabled to "));
    CHECK(!said("contradict"));

    /* three tiles in a row and a cable from the first to the last that says
     * the last is above the first: one problem, reported once though both
     * ends see it */
    start(3);
    cable(0, PORT_W, 1, PORT_E);
    cable(1, PORT_W, 2, PORT_E);
    cable(0, PORT_N, 2, PORT_S);
    settle();
    CHECK_EQ(placed(), 3);
    CHECK_EQ(h.nproblems, 1);
    CHECK_EQ(h.problem[0].what, PROBLEM_PLACE);
    /* the hub's tile's own cables are believed first */
    CHECK_EQ(T(0)->u, 1);
    CHECK_EQ(T(0)->v, 0);
    CHECK_EQ(T(1)->u, 0);
    CHECK_EQ(T(1)->v, 0);
    CHECK_EQ(T(2)->u, 1);
    CHECK_EQ(T(2)->v, 1);
    say("grid");
    CHECK_EQ(times_said("problem:"), 1);
    CHECK(said("which the other cables contradict"));

    /* two tiles cabled into the same place: one gets it */
    start(5);
    cable(0, PORT_W, 1, PORT_E);
    cable(0, PORT_N, 2, PORT_S);
    cable(1, PORT_N, 3, PORT_S);
    cable(2, PORT_W, 4, PORT_E);
    settle();
    CHECK_EQ(placed(), 4);
    CHECK(T(3)->placed != T(4)->placed);
    CHECK_EQ(h.nproblems, 1);
    CHECK_EQ(h.problem[0].what, PROBLEM_PLACE);

    /* a tile with another key layout is known but not laid out */
    start(2);
    cable(0, PORT_N, 1, PORT_S);
    fake[1].cols = 4;
    settle();
    CHECK(!T(1)->placed);
    CHECK_EQ(h.nproblems, 0);
    say("tiles");
    CHECK(said("(4 x 5 keys: not supported)"));

    /* nobody on the hub's cable: no tile has a place */
    start(2);
    cable(0, PORT_N, 1, PORT_S);
    settle();
    CHECK_EQ(placed(), 2);
    fake[0].alive = fake[1].alive = false;
    run(LINK_UP_MS + 5);
    CHECK_EQ(hub_root(&h), ID_NONE);
    CHECK_EQ(used(), 2);
    CHECK_EQ(placed(), 0);
    CHECK_EQ(h.width, 0);
    CHECK_EQ(h.height, 0);
    say("grid");
    CHECK(said("hub: no tile on its cable\r\n0 x 0\r\nunplaced: 0 1\r\nok\r\n"));

    /* a tile that claims to be its own neighbour, or the hub's */
    start(2);
    cable(0, PORT_W, 0, PORT_E);
    fake[1].nbr_id[PORT_S] = h.node.id;
    settle();
    CHECK_EQ(placed(), 1);
    CHECK_EQ(h.nproblems, 0);
}

/* ------------------------------------------------------------------ notes --- */

static void notes(void)
{
    start(2);
    settle();                   /* tile 1 is not cabled: it has no place */

    note(0, 0, true, 1000);
    note(0, 7, true, 3000);
    note(0, 0, false, 500);
    drain();
    CHECK_EQ(nmidi, 3);
    CHECK_MIDI(0, 0x90, 48, notes_velocity(&h.set, 1000));
    CHECK_MIDI(1, 0x90, 66, notes_velocity(&h.set, 3000));
    CHECK_MIDI(2, 0x80, 48, notes_velocity(&h.set, 500));
    CHECK(midi[0][2] > 1 && midi[0][2] < midi[1][2] && midi[1][2] < 127);
    CHECK_EQ(h.held[66], 1);
    CHECK_EQ(h.held[48], 0);

    /* the same frame again, and an older one: counted, not played */
    clear();
    note_raw(0, fake[0].events, 0, false, 500);
    note_raw(0, (uint8_t)(fake[0].events - 2), 0, true, 1000);
    drain();
    CHECK_EQ(nmidi, 0);
    CHECK_EQ(h.stats.notes_old, 2);
    CHECK_EQ(h.stats.notes_lost, 0);

    /* a gap in the counter: two frames never came; this one is played */
    fake[0].events += 2;
    note(0, 7, false, 700);
    drain();
    CHECK_EQ(nmidi, 1);
    CHECK_MIDI(0, 0x80, 66, notes_velocity(&h.set, 700));
    CHECK_EQ(h.stats.notes_lost, 2);

    /* a key that does not exist */
    clear();
    note_raw(0, (uint8_t)(fake[0].events + 1), 40, true, 1000);
    note_raw(0, (uint8_t)(fake[0].events + 1), 0x7F, true, 1000);
    drain();
    CHECK_EQ(nmidi, 0);

    /* the counter wraps */
    fake[0].events = 0xFE;
    state_raw(0, 0xFE, fake[0].bitmap);
    state_raw(0, 0xFE, fake[0].bitmap);
    T(0)->events = 0xFE;
    clear();
    note(0, 1, true, 1000);     /* 0xFF */
    note(0, 1, false, 1000);    /* 0x00 */
    note(0, 1, true, 1000);     /* 0x01 */
    drain();
    CHECK_EQ(nmidi, 3);
    CHECK_EQ(h.stats.notes_lost, 2);
    CHECK_EQ(h.stats.notes_old, 2);
    note(0, 1, false, 1000);
    drain();

    /* another channel */
    h.set.channel = 5;
    clear();
    note(0, 2, true, 1000);
    note(0, 2, false, 1000);
    drain();
    CHECK_MIDI(0, 0x95, 48 + 14, notes_velocity(&h.set, 1000));
    CHECK_MIDI(1, 0x85, 48 + 14, notes_velocity(&h.set, 1000));
    h.set.channel = 0;

    /* a tile without a place has no notes; its keys are still followed */
    clear();
    note(1, 3, true, 1000);
    drain();
    CHECK_EQ(nmidi, 0);
    CHECK(KEY_BIT(T(1)->on, 3));
    note(1, 3, false, 1000);
    drain();
    CHECK_EQ(nmidi, 0);
    CHECK(!KEY_BIT(T(1)->on, 3));

    /* a note off the end of MIDI's range is not played */
    h.set.base = 127;
    clear();
    note(0, 0, true, 1000);     /* 127 */
    note(0, 1, true, 1000);     /* 134 */
    note(0, 0, false, 1000);
    note(0, 1, false, 1000);
    drain();
    CHECK_EQ(nmidi, 2);
    CHECK_MIDI(0, 0x90, 127, notes_velocity(&h.set, 1000));
    CHECK_MIDI(1, 0x80, 127, notes_velocity(&h.set, 1000));

    /* an on for a key that is on already: its off was lost */
    h.set.base = 48;
    clear();
    note(0, 5, true, 1000);
    fake[0].events++;           /* the off, lost */
    note(0, 5, true, 2000);
    drain();
    CHECK_EQ(nmidi, 3);
    CHECK_MIDI(1, 0x80, midi[0][1], 64);
    CHECK_MIDI(2, 0x90, midi[0][1], notes_velocity(&h.set, 2000));
    CHECK_EQ(h.held[midi[0][1]], 1);
}

/* Keys 4 and 38 of one tile are the same note. */
static void same_pitch(void)
{
    start(1);
    settle();
    CHECK_EQ(notes_note(&h.set, 0, 0, 4), 76);
    CHECK_EQ(notes_note(&h.set, 0, 0, 38), 76);

    note(0, 4, true, 1000);
    drain();
    CHECK_EQ(nmidi, 1);
    CHECK_MIDI(0, 0x90, 76, notes_velocity(&h.set, 1000));

    note(0, 38, true, 2000);    /* struck again */
    drain();
    CHECK_EQ(nmidi, 3);
    CHECK_MIDI(1, 0x80, 76, 64);
    CHECK_MIDI(2, 0x90, 76, notes_velocity(&h.set, 2000));

    note(0, 4, false, 500);     /* the other key still holds it */
    drain();
    CHECK_EQ(nmidi, 3);

    note(0, 38, false, 600);
    drain();
    CHECK_EQ(nmidi, 4);
    CHECK_MIDI(3, 0x80, 76, notes_velocity(&h.set, 600));
    CHECK_EQ(h.held[76], 0);
}

static void state_heals(void)
{
    uint8_t mid;

    start(1);
    settle();
    fake[0].alive = false;      /* its STATEs by hand from here */
    mid = notes_velocity_mid(&h.set);
    CHECK(mid > 50 && mid < 80);

    /* a note on that got lost */
    key_moves(0, 3, true);
    send_state(0);
    drain();
    CHECK_EQ(nmidi, 0);         /* once is not enough: the NOTE may be on its way */
    send_state(0);
    drain();
    CHECK_EQ(nmidi, 1);
    CHECK_MIDI(0, 0x90, notes_note(&h.set, 0, 0, 3), mid);
    CHECK_EQ(h.stats.healed_on, 1);
    send_state(0);
    drain();
    CHECK_EQ(nmidi, 1);

    /* its note off arrives: played; the hub knows one frame was lost */
    note(0, 3, false, 900);
    drain();
    CHECK_EQ(nmidi, 2);
    CHECK_MIDI(1, 0x80, notes_note(&h.set, 0, 0, 3), notes_velocity(&h.set, 900));
    CHECK_EQ(h.stats.notes_lost, 1);

    /* a note off that got lost */
    clear();
    note(0, 9, true, 1000);
    key_moves(0, 9, false);
    send_state(0);
    send_state(0);
    drain();
    CHECK_EQ(nmidi, 2);
    CHECK_MIDI(1, 0x80, notes_note(&h.set, 0, 0, 9), 64);
    CHECK_EQ(h.stats.healed_off, 1);

    /* a STATE that overtook its NOTE: the NOTE is played as it is, and
     * nothing is put right */
    clear();
    key_moves(0, 5, true);
    send_state(0);
    note_raw(0, fake[0].events, 5, true, 4000);
    send_state(0);
    send_state(0);
    drain();
    CHECK_EQ(nmidi, 1);
    CHECK_MIDI(0, 0x90, notes_note(&h.set, 0, 0, 5), notes_velocity(&h.set, 4000));
    CHECK_EQ(h.stats.healed_on, 1);
    CHECK_EQ(h.stats.notes_old, 0);

    /* one old STATE (from before the key went down) changes nothing */
    uint8_t none[5] = {0};
    clear();
    state_raw(0, (uint8_t)(fake[0].events - 1), none);
    send_state(0);
    state_raw(0, (uint8_t)(fake[0].events - 1), none);
    send_state(0);
    drain();
    CHECK_EQ(nmidi, 0);
    CHECK_EQ(h.stats.healed_off, 1);

    /* The tile started again: it counts from 0 and holds nothing. Two
     * STATEs to believe it, one more to be sure about the key. */
    fake[0].events = 0;
    memset(fake[0].bitmap, 0, 5);
    T(0)->events = 100;         /* far enough along that 0 looks old */
    send_state(0);
    send_state(0);
    drain();
    CHECK_EQ(nmidi, 0);
    send_state(0);
    drain();
    CHECK_EQ(nmidi, 1);
    CHECK_MIDI(0, 0x80, notes_note(&h.set, 0, 0, 5), 64);
    clear();
    note(0, 6, true, 1000);     /* its first note is not taken for an old one */
    drain();
    CHECK_EQ(nmidi, 1);
    CHECK_EQ(h.stats.notes_old, 0);

    /* two keys wrong at once, in different bytes */
    start(1);
    settle();
    fake[0].alive = false;
    key_moves(0, 0, true);
    key_moves(0, 39, true);
    send_state(0);
    send_state(0);
    drain();
    CHECK_EQ(nmidi, 2);
    CHECK_EQ(h.stats.healed_on, 2);
    CHECK(KEY_BIT(T(0)->on, 0) && KEY_BIT(T(0)->on, 39));

    /* bits past key 39 in the last byte mean nothing */
    uint8_t odd[5];
    memcpy(odd, fake[0].bitmap, 5);
    clear();
    state_raw(0, fake[0].events, odd);
    state_raw(0, fake[0].events, odd);
    drain();
    CHECK_EQ(nmidi, 0);
}

static void aftertouch(void)
{
    uint8_t pos[4] = {3, 200, 9, 100};

    start(1);
    settle();
    note(0, 3, true, 1000);
    drain();
    clear();

    up(fake[0].id, FT_POS, pos, sizeof pos);
    drain();
    CHECK_EQ(nmidi, 0);         /* not wanted */

    h.set.aftertouch = 1;
    up(fake[0].id, FT_POS, pos, sizeof pos);
    drain();
    CHECK_EQ(nmidi, 1);         /* key 9 is not on: nothing to press harder */
    CHECK_MIDI(0, 0xA0, notes_note(&h.set, 0, 0, 3), 100);

    uint8_t odd[3] = {3, 255, 44};      /* half a pair at the end; a key too many */
    clear();
    up(fake[0].id, FT_POS, odd, sizeof odd);
    drain();
    CHECK_EQ(nmidi, 1);
    CHECK_MIDI(0, 0xA0, notes_note(&h.set, 0, 0, 3), 127);
}

/* ---------------------------------------------------------------- console --- */

static void console_basics(void)
{
    char want[160];

    start(3);
    cable(0, PORT_W, 1, PORT_E);
    fake[1].flags = INFO_CALIBRATED | INFO_RAW;
    fake[1].parent = PORT_E;
    fake[1].hops = 1;
    settle();

    say("help");
    CHECK(said("tiles | grid | info <tile>"));
    CHECK(said("bridge <baud>"));
    CHECK(ends("ok"));

    say("version");
    snprintf(want, sizeof want, "piano hub, firmware 0001, protocol 1, id %08x\r\nok\r\n",
             (unsigned)h.node.id);
    CHECK(!strcmp(text, want));

    say("frobnicate");
    CHECK(!strcmp(text, "error: no such command (try help)\r\n"));

    /* an empty line says nothing; neither do spaces */
    say("");
    CHECK_EQ(ntext, 0);
    say("   ");
    CHECK_EQ(ntext, 0);

    /* CR LF is one line end, not two commands */
    clear();
    hub_console_rx(&h, (const uint8_t *)"version\r\n", 9, now);
    drain();
    CHECK_EQ(times_said("piano hub"), 1);
    CHECK_EQ(times_said("ok"), 1);

    /* backspace and delete */
    say("versiox\bn");
    CHECK(said("piano hub"));
    say("x\x7f\x7f\x7fversion");
    CHECK(said("piano hub"));

    /* extra spaces */
    say("  get    base  ");
    CHECK(!strcmp(text, "base = 48\r\nok\r\n"));

    /* the longest line, and one longer */
    char line[HUB_LINE_MAX + 2];
    memset(line, 'a', sizeof line);
    line[HUB_LINE_MAX] = 0;
    say(line);
    CHECK(said("no such command"));
    line[HUB_LINE_MAX] = 'a';
    line[HUB_LINE_MAX + 1] = 0;
    say(line);
    CHECK(!strcmp(text, "error: line too long\r\n"));
    say("version");             /* and the next line is a fresh one */
    CHECK(said("piano hub"));

    /* characters that are not text are dropped */
    say("ver\x01sion\t");
    CHECK(said("piano hub"));

    say("tiles");
    snprintf(want, sizeof want, "0 %08x at 1,0 parent E hops 0 fw 0001\r\n", (unsigned)fake[0].id);
    CHECK(said(want));
    snprintf(want, sizeof want, "1 %08x at 0,0 parent E hops 1 fw 0001 calibrated raw\r\n",
             (unsigned)fake[1].id);
    CHECK(said(want));
    snprintf(want, sizeof want, "2 %08x unplaced parent - fw 0001\r\n", (unsigned)fake[2].id);
    CHECK(said(want));
    CHECK(ends("ok"));
    CHECK(said("3 tiles\r\n"));

    say("grid");
    CHECK(!strcmp(text, "hub on tile 0\r\n2 x 1\r\n  1  0\r\nunplaced: 2\r\nok\r\n"));

    note(0, 3, true, 1000);
    say("info 0");
    snprintf(want, sizeof want, "tile 0 id %08x at 1,0\r\n", (unsigned)fake[0].id);
    CHECK(said(want));
    CHECK(said(" ms ago, 1 keys down\r\n"));
    CHECK(said("firmware 0001, 8 x 5 keys, not calibrated\r\n"));
    CHECK(said("to the hub: port E, 0 tiles between\r\n"));
    CHECK(said("W: tile 1 port E\r\nN: nobody\r\nE: the hub\r\nS: nobody\r\n"));
    CHECK(said("up 12 s, 3 bad frames, 4 frames dropped from a full queue\r\nok\r\n"));

    /* a tile by its place, and by its ID */
    say("info 0,0");
    CHECK(said("tile 1 id "));
    CHECK(said("8 x 5 keys, calibrated raw\r\n"));
    CHECK(said("E: tile 0 port W\r\n"));
    snprintf(want, sizeof want, "info %08x", (unsigned)fake[2].id);
    say(want);
    CHECK(said("tile 2 id "));
    CHECK(said("unplaced\r\n"));
    CHECK(said("no way to the hub\r\n"));

    say("info");
    CHECK(!strcmp(text, "error: usage: info <tile>\r\n"));
    say("info 7");
    CHECK(!strcmp(text, "error: no such tile\r\n"));
    say("info 5,5");
    CHECK(!strcmp(text, "error: no such tile\r\n"));
    say("info -1");
    CHECK(!strcmp(text, "error: no such tile\r\n"));
    say("info 99999999999");
    CHECK(!strcmp(text, "error: no such tile\r\n"));

    say("stats");
    CHECK(said("notes: 0 late, 0 lost, 0 started and 0 stopped by STATE\r\n"));
    CHECK(ends("ok"));

    /* a tile with no INFO yet */
    start(1);
    send_state(0);
    say("tiles");
    CHECK(said("unplaced (no info yet)\r\n1 tile\r\nok\r\n"));
    say("info 0");
    CHECK(said("no info yet\r\nok\r\n"));

    /* the hub says nothing unless asked: three seconds of playing */
    start(2);
    cable(0, PORT_N, 1, PORT_S);
    settle();
    for (int i = 0; i < 30; i++) {
        note(i & 1, (uint8_t)i, true, 1000);
        run(50);
        note(i & 1, (uint8_t)i, false, 1000);
        run(50);
    }
    uint8_t log[5] = {'h', 'e', 'l', 'l', 'o'};
    up(fake[0].id, FT_LOG, log, sizeof log);
    drain();
    CHECK_EQ(ntext, 0);
    CHECK_EQ(nmidi, 60);
}

static void settings(void)
{
    start(1);
    settle();

    say("get");
    CHECK(!strcmp(text, "base = 48\r\nstepa = 4\r\nstepb = 7\r\nchannel = 1\r\nvel1 = 100\r\n"
                        "vel127 = 12000\r\nbend = 0\r\naftertouch = 0\r\npd = 15\r\nok\r\n"));
    say("set base 60");
    CHECK(!strcmp(text, "ok\r\n"));
    CHECK_EQ(h.set.base, 60);
    say("set base -500");
    CHECK_EQ(h.set.base, -500);
    say("get base");
    CHECK(!strcmp(text, "base = -500\r\nok\r\n"));
    say("set base 501");
    CHECK(!strcmp(text, "error: out of range\r\n"));
    CHECK_EQ(h.set.base, -500);
    say("set base");
    CHECK(!strcmp(text, "error: usage: set <name> <number>\r\n"));
    say("set base 1x");
    CHECK(said("usage"));
    say("set base -");
    CHECK(said("usage"));
    say("set nosuch 1");
    CHECK(!strcmp(text, "error: no such setting\r\n"));
    say("get nosuch");
    CHECK(!strcmp(text, "error: no such setting\r\n"));

    say("set channel 16");
    CHECK_EQ(h.set.channel, 15);
    say("get channel");
    CHECK(said("channel = 16"));
    say("set channel 0");
    CHECK(said("out of range"));
    say("set channel 17");
    CHECK(said("out of range"));

    say("set vel127 50");
    CHECK(!strcmp(text, "error: vel127 has to be above vel1\r\n"));
    CHECK_EQ(h.set.vel127, 12000);
    say("set vel1 12000");
    CHECK(said("vel127 has to be above vel1"));
    say("set vel1 300");
    CHECK_EQ(h.set.vel1, 300);
    say("set bend -100");
    CHECK_EQ(h.set.bend, -100);
    say("set bend 101");
    CHECK(said("out of range"));
    say("set aftertouch 1");
    CHECK_EQ(h.set.aftertouch, 1);
    say("set aftertouch 2");
    CHECK(said("out of range"));
    say("set stepa -24");
    CHECK_EQ(h.set.step_a, -24);
    say("set stepb 25");
    CHECK(said("out of range"));

    /* the charger: 20 V is more than the board takes */
    say("set pd 9");
    CHECK(!strcmp(text, "ok\r\n"));
    CHECK_EQ(h.set.pd_volts, 9);
    CHECK_EQ(h.power.pd_set, 9);
    say("set pd 7");
    CHECK(!strcmp(text, "error: pd is 5, 9, 12 or 15\r\n"));
    say("set pd 20");
    CHECK(said("out of range"));
    CHECK_EQ(h.set.pd_volts, 9);
    CHECK_EQ(h.power.pd_set, 9);

    /* layouts */
    start(1);
    settle();
    say("layout");
    CHECK(!strcmp(text, "harmonic 4 7\r\nok\r\n"));
    say("layout janko");
    CHECK_EQ(h.set.step_a, 1);
    CHECK_EQ(h.set.step_b, 2);
    say("layout");
    CHECK(said("janko 1 2"));
    say("layout fifths");
    CHECK_EQ(h.set.step_a, 7);
    CHECK_EQ(h.set.step_b, 12);
    say("layout custom 3 -5");
    CHECK_EQ(h.set.step_a, 3);
    CHECK_EQ(h.set.step_b, -5);
    say("layout");
    CHECK(said("custom 3 -5"));
    say("layout custom 30 1");
    CHECK(said("out of range"));
    say("layout custom 3");
    CHECK(said("usage: layout custom <a> <b>"));
    say("layout nosuch");
    CHECK(!strcmp(text, "error: no such layout\r\n"));
    say("layout harmonic");
    say("set stepa 4");
    say("layout");
    CHECK(said("harmonic 4 7"));

    /* A key is held while the base changes: its note off is for the note
     * that was started. */
    note(0, 0, true, 1000);
    say("set base 60");
    CHECK_EQ(nmidi, 0);
    note(0, 0, false, 1000);
    drain();
    CHECK_EQ(nmidi, 1);
    CHECK_MIDI(0, 0x80, 48, notes_velocity(&h.set, 1000));
    note(0, 0, true, 1000);
    drain();
    CHECK_MIDI(1, 0x90, 60, notes_velocity(&h.set, 1000));
    note(0, 0, false, 1000);

    /* A key is held while the channel changes: the note stops on the old
     * channel and does not start by itself on the new one. */
    note(0, 2, true, 1000);
    say("set channel 3");
    CHECK_EQ(nmidi, 1);
    CHECK_MIDI(0, 0x80, 60 + 14, 64);
    CHECK_EQ(h.held[74], 0);
    run(350);                   /* three STATEs that still show the key down */
    CHECK_EQ(nmidi, 1);
    CHECK_EQ(h.stats.healed_on, 0);
    note(0, 2, false, 1000);
    drain();
    CHECK_EQ(nmidi, 1);
    note(0, 2, true, 1000);
    drain();
    CHECK_EQ(nmidi, 2);
    CHECK_MIDI(1, 0x92, 74, notes_velocity(&h.set, 1000));

    /* panic: every note off, and the controller that says so */
    note(0, 9, true, 1000);
    say("panic");
    CHECK_EQ(nmidi, 3);
    CHECK_MIDI(0, 0x82, 74, 64);
    CHECK_MIDI(1, 0x82, notes_note(&h.set, 0, 0, 9), 64);
    CHECK_MIDI(2, 0xB2, 123, 0);
    CHECK(!strcmp(text, "ok\r\n"));
    run(350);
    CHECK_EQ(nmidi, 3);
    say("panic");
    CHECK_EQ(nmidi, 1);
    CHECK_MIDI(0, 0xB2, 123, 0);
}

/* --------------------------------------------------------------- commands --- */

static void commands(void)
{
    start(3);
    settle();

    /* one command to every tile, one after another */
    say("tset all lo 200");
    CHECK(!strcmp(text, "ok\r\n"));
    CHECK_EQ(ncmds, 3);
    for (int i = 0; i < 3 && i < ncmds; i++) {
        CHECK_EQ(cmds[i].target, fake[i].id);
        CHECK_EQ(cmds[i].op, OP_SET);
        CHECK_EQ(cmds[i].arg_len, 5);
        CHECK_EQ(cmds[i].arg[0], PARAM_LO);
        CHECK_EQ(get32(cmds[i].arg + 1), 200);
        CHECK_EQ(fake[i].runs, 1);
    }
    CHECK(cmds[0].counter != cmds[1].counter && cmds[1].counter != cmds[2].counter);

    /* a negative value */
    say("tset 1 swing -600");
    CHECK_EQ(ncmds, 1);
    CHECK_EQ(cmds[0].target, fake[1].id);
    CHECK_EQ(cmds[0].arg[0], PARAM_SWING);
    CHECK_EQ((int32_t)get32(cmds[0].arg + 1), -600);

    /* the tile misses the command twice: three copies 100 ms apart, the
     * same counter, run once */
    fake[0].deaf = 2;
    fake[0].runs = 0;
    say("tset 0 hi 700");
    CHECK(!strcmp(text, "ok\r\n"));
    CHECK_EQ(ncmds, 3);
    CHECK_EQ(cmds[1].at - cmds[0].at, CMD_RETRY_MS);
    CHECK_EQ(cmds[2].at - cmds[1].at, CMD_RETRY_MS);
    CHECK(cmds[0].counter == cmds[1].counter && cmds[1].counter == cmds[2].counter);
    CHECK_EQ(fake[0].runs, 1);

    /* the reply gets lost: the repeat is answered and not run again */
    fake[0].mute = 1;
    fake[0].runs = 0;
    say("tset 0 hi 650");
    CHECK(!strcmp(text, "ok\r\n"));
    CHECK_EQ(ncmds, 2);
    CHECK_EQ(fake[0].heard, 6);
    CHECK_EQ(fake[0].runs, 1);

    /* nobody answers: five copies, then the hub gives up and says so */
    fake[0].deaf = 1000;
    uint32_t t0 = now;
    say("tset 0 hi 600");
    CHECK(!strcmp(text, "tile 0: no answer\r\nerror: 1 failed\r\n"));
    CHECK_EQ(ncmds, CMD_TRIES);
    CHECK_EQ(cmds[CMD_TRIES - 1].at - cmds[0].at, (CMD_TRIES - 1) * CMD_RETRY_MS);
    CHECK_NEAR(now - t0, CMD_TRIES * CMD_RETRY_MS, 3);
    CHECK_EQ(h.stats.no_answer, 1);

    /* with all: the others still get theirs */
    fake[1].runs = fake[2].runs = 0;
    say("tset all release 400");
    CHECK(!strcmp(text, "tile 0: no answer\r\nerror: 1 failed\r\n"));
    CHECK_EQ(fake[1].runs, 1);
    CHECK_EQ(fake[2].runs, 1);
    fake[0].deaf = 0;

    /* reading a parameter */
    say("tget 0 hi");
    CHECK(!strcmp(text, "tile 0: hi = 102\r\nok\r\n"));
    CHECK_EQ(cmds[0].op, OP_GET);
    CHECK_EQ(cmds[0].arg_len, 1);
    CHECK_EQ(cmds[0].arg[0], PARAM_HI);
    say("tget all swing");
    CHECK(!strcmp(text, "tile 0: swing = 108\r\ntile 1: swing = 108\r\ntile 2: swing = 108\r\nok\r\n"));

    /* a tile that says no */
    fake[1].status = ST_BAD_ARG;
    say("tset 1 lo 5");
    CHECK(!strcmp(text, "tile 1: value not allowed\r\nerror: 1 failed\r\n"));
    fake[1].status = ST_UNKNOWN;
    say("tset 1 lo 5");
    CHECK(said("tile 1: does not know that"));
    fake[1].status = ST_FAILED;
    say("save 1");
    CHECK(said("tile 1: failed"));
    fake[1].status = ST_BUSY;
    say("save 1");
    CHECK(said("tile 1: busy"));
    fake[1].status = 77;
    say("ping 1");
    CHECK(said("tile 1: odd reply"));
    fake[1].status = ST_OK;

    /* calibration */
    say("cal start");
    CHECK(!strcmp(text, "ok\r\n"));
    CHECK_EQ(ncmds, 3);
    CHECK_EQ(cmds[0].op, OP_CAL);
    CHECK_EQ(cmds[0].arg[0], CAL_START);
    say("cal stop");
    CHECK(!strcmp(text, "tile 0: 37 of 40 keys measured\r\ntile 1: 37 of 40 keys measured\r\n"
                        "tile 2: 37 of 40 keys measured\r\nok\r\n"));
    CHECK_EQ(cmds[0].arg[0], CAL_STOP);
    say("cal forget 2");
    CHECK(!strcmp(text, "ok\r\n"));
    CHECK_EQ(ncmds, 1);
    CHECK_EQ(cmds[0].target, fake[2].id);
    CHECK_EQ(cmds[0].arg[0], CAL_FORGET);
    say("cal");
    CHECK(said("usage: cal start|stop|forget [tile|all]"));
    say("cal maybe");
    CHECK(said("usage"));

    /* ping */
    say("ping");
    CHECK_EQ(times_said(" ms\r\n"), 3);
    CHECK(said("tile 2: "));
    CHECK(ends("ok"));
    CHECK_EQ(cmds[0].op, OP_PING);
    CHECK_EQ(cmds[0].arg_len, 0);
    say("ping 1");
    CHECK_EQ(times_said(" ms\r\n"), 1);

    /* things that cannot be asked */
    say("tset 9 lo 1");
    CHECK(!strcmp(text, "error: no such tile\r\n"));
    say("tset 0 nosuch 1");
    CHECK(!strcmp(text, "error: no such parameter\r\n"));
    say("tget 0 nosuch");
    CHECK(!strcmp(text, "error: no such parameter\r\n"));
    say("tset 0 lo");
    CHECK(said("usage: tset <tile|all> <name> <number>"));
    say("tget 0");
    CHECK(said("usage: tget <tile|all> <name>"));
    CHECK_EQ(ncmds, 0);

    /* While a command is out, typing waits: the caller keeps the rest. */
    const char *two = "ping 0\nversion\n";
    clear();
    size_t took = hub_console_rx(&h, (const uint8_t *)two, strlen(two), now);
    CHECK_EQ(took, 7);
    CHECK_EQ(hub_console_rx(&h, (const uint8_t *)two + took, strlen(two) - took, now), 0);
    wait_done();
    CHECK(said("tile 0: "));
    CHECK(!said("piano hub"));
    CHECK_EQ(hub_console_rx(&h, (const uint8_t *)two + took, strlen(two) - took, now),
             strlen(two) - took);
    drain();
    CHECK(said("piano hub"));

    /* a tile that goes away while its command is out */
    fake[2].alive = false;
    run(TILE_FORGET_MS - 300);
    type("ping all");
    run(600);
    wait_done();
    CHECK(said("tile 0: "));
    CHECK(said("tile 1: "));
    CHECK(said(": gone\r\n") || said("tile 2: no answer\r\n"));
    CHECK(ends("error: 1 failed"));

    /* no tiles at all */
    start(0);
    say("ping");
    CHECK(!strcmp(text, "error: no tiles\r\n"));
    say("cal start");
    CHECK(!strcmp(text, "error: no tiles\r\n"));
    say("ping 0");
    CHECK(!strcmp(text, "error: no such tile\r\n"));
}

/* The counter of a new command is never the one the tile saw last. */
static void counters(void)
{
    start(2);
    settle();

    for (int i = 0; i < 600; i++)
        say("ping 0");
    CHECK_EQ(fake[0].runs, 600);
    CHECK_EQ(fake[0].heard, 600);

    /* 255 commands to somebody else in between: the counter has come round */
    fake[0].runs = 0;
    say("ping 0");
    uint8_t first = cmds[0].counter;
    for (int i = 0; i < 255; i++)
        say("ping 1");
    say("ping 0");
    CHECK(cmds[0].counter != first);
    CHECK_EQ(fake[0].runs, 2);

    /* the same for the commands that go to everybody */
    say("adc off");
    first = cmds[0].counter;
    for (int i = 0; i < 255; i++)
        say("ping 1");
    say("adc off");
    CHECK(cmds[0].counter != first);
    CHECK_EQ(fake[0].bcast_runs, 2);
    CHECK_EQ(fake[1].bcast_runs, 2);
}

static void broadcast(void)
{
    start(3);
    settle();

    /* three copies, 5 ms apart, one counter; every tile runs it once */
    say("reboot all");
    CHECK(!strcmp(text, "ok\r\n"));
    CHECK_EQ(ncmds, BCAST_REPEATS);
    for (int i = 0; i < BCAST_REPEATS && i < ncmds; i++) {
        CHECK_EQ(cmds[i].target, ID_ALL);
        CHECK_EQ(cmds[i].op, OP_REBOOT);
        CHECK_EQ(cmds[i].arg_len, 0);
        CHECK_EQ(cmds[i].counter, cmds[0].counter);
        if (i)
            CHECK_EQ(cmds[i].at - cmds[i - 1].at, BCAST_GAP_MS);
    }
    for (int i = 0; i < 3; i++)
        CHECK_EQ(fake[i].bcast_runs, 1);
    CHECK_EQ(fake[0].runs, 0);
    uint8_t before = cmds[0].counter;

    /* quiet: the tiles are told, and the hub keeps quiet itself */
    say("quiet 300");
    CHECK(!strcmp(text, "ok\r\n"));
    CHECK_EQ(ncmds, BCAST_REPEATS);
    CHECK_EQ(cmds[0].op, OP_QUIET);
    CHECK_EQ(cmds[0].arg_len, 2);
    CHECK_EQ(get16(cmds[0].arg), 300);
    CHECK(cmds[0].counter != before);
    CHECK_EQ(fake[1].bcast_runs, 2);
    /* the hub's own quiet starts no later than any tile's */
    uint32_t from = h.node.quiet_from, until = h.node.quiet_until;
    CHECK_EQ(until - from, 300);
    CHECK(from >= cmds[0].at && from <= cmds[0].at + QUIET_LEAD_MS);
    CHECK(from >= cmds[BCAST_REPEATS - 1].at);  /* every copy is out by then */

    say("ping 0");
    CHECK(!strcmp(text, "error: the cable is quiet\r\n"));
    say("reboot all");
    CHECK(!strcmp(text, "error: the cable is quiet\r\n"));
    say("adc off");
    CHECK(!strcmp(text, "error: the cable is quiet\r\n"));
    say("quiet 5");
    CHECK(!strcmp(text, "error: the cable is quiet\r\n"));

    CHECK(now < from);
    while (now < from)
        step();
    clear();
    while (now < until - 1)
        step();
    CHECK_EQ(frames, 0);        /* not a beacon, not a hello */
    run(120);
    CHECK(beacons >= 1 && beacons <= 3);
    say("ping 0");
    CHECK(ends("ok"));

    say("quiet");
    CHECK(said("usage: quiet <ms>, 1 to 65535"));
    say("quiet 0");
    CHECK(said("usage"));
    say("quiet 65536");
    CHECK(said("usage"));
    say("reboot");
    CHECK(said("usage: reboot hub|all|<tile>"));

    /* one tile only */
    say("reboot 1");
    CHECK(!strcmp(text, "ok\r\n"));
    CHECK_EQ(ncmds, 1);
    CHECK_EQ(cmds[0].target, fake[1].id);
    CHECK_EQ(cmds[0].op, OP_REBOOT);
}

/* ------------------------------------------------------- flash and restart --- */

static void save(void)
{
    uint8_t image[STORE_HUB_LEN];
    hub_settings_t s;
    hub_t h2;

    start(2);
    settle();
    say("set base 36");
    say("layout janko");
    say("set channel 4");
    say("set vel1 200");
    say("set vel127 9000");
    say("set bend -30");
    say("set aftertouch 1");
    say("set pd 12");

    /* the hub's own settings */
    type("save");
    run(3);
    CHECK_EQ(acts, HUB_ACT_SAVE);
    CHECK_EQ(ntext, 0);         /* not done yet */
    CHECK_EQ(ncmds, 0);
    hub_save_image(&h, image);
    CHECK(store_hub_unpack(image, sizeof image, &s));
    CHECK(!memcmp(&s, &h.set, sizeof s));
    CHECK_EQ(s.base, 36);
    CHECK_EQ(s.step_a, 1);
    CHECK_EQ(s.channel, 3);
    CHECK_EQ(s.pd_volts, 12);
    run(500);
    CHECK_EQ(ntext, 0);
    hub_save_done(&h, true);
    run(2);
    CHECK(!strcmp(text, "ok\r\n"));

    /* a hub that starts with that record has those settings */
    hub_init(&h2, UID, image, sizeof image, 5);
    CHECK(!memcmp(&h2.set, &h.set, sizeof s));
    CHECK_EQ(h2.power.pd_set, 12);
    /* ... with a damaged one, or none, the defaults */
    image[9] ^= 1;
    hub_init(&h2, UID, image, sizeof image, 5);
    settings_default(&s);
    CHECK(!memcmp(&h2.set, &s, sizeof s));
    hub_init(&h2, UID, image, 3, 5);
    CHECK(!memcmp(&h2.set, &s, sizeof s));
    hub_init(&h2, UID, NULL, 0, 5);
    CHECK(!memcmp(&h2.set, &s, sizeof s));
    /* ... and with a whole record of values it cannot use, the defaults too */
    hub_settings_t bad = h.set;
    bad.pd_volts = 20;
    store_hub_pack(&bad, image);
    hub_init(&h2, UID, image, sizeof image, 5);
    CHECK(!memcmp(&h2.set, &s, sizeof s));

    /* the flash fails */
    type("save hub");
    run(3);
    CHECK_EQ(acts, HUB_ACT_SAVE);
    hub_save_done(&h, false);
    run(2);
    CHECK(!strcmp(text, "hub: flash failed\r\nerror: 1 failed\r\n"));

    /* the port never says how it went */
    type("save");
    run(1999);
    CHECK_EQ(ntext, 0);
    run(5);
    CHECK(!strcmp(text, "hub: flash failed\r\nerror: 1 failed\r\n"));
    hub_save_done(&h, true);    /* too late: nothing waits for it */
    run(5);
    CHECK(!strcmp(text, "hub: flash failed\r\nerror: 1 failed\r\n"));

    /* everything: the tiles first, then the hub */
    type("save all");
    run(10);
    CHECK_EQ(ncmds, 2);
    CHECK_EQ(cmds[0].op, OP_SAVE);
    CHECK_EQ(cmds[0].target, fake[0].id);
    CHECK_EQ(cmds[1].target, fake[1].id);
    CHECK_EQ(acts, HUB_ACT_SAVE);
    CHECK(act_at >= cmds[1].at);
    CHECK_EQ(ntext, 0);
    hub_save_done(&h, true);
    run(2);
    CHECK(!strcmp(text, "ok\r\n"));

    /* a tile that cannot: the hub still saves its own, and says what failed */
    fake[1].status = ST_FAILED;
    type("save all");
    run(10);
    hub_save_done(&h, true);
    run(2);
    CHECK(!strcmp(text, "tile 1: failed\r\nerror: 1 failed\r\n"));
    CHECK_EQ(acts, HUB_ACT_SAVE);
    fake[1].status = ST_OK;

    /* one tile: the hub's flash is left alone */
    say("save 1");
    CHECK(!strcmp(text, "ok\r\n"));
    CHECK_EQ(ncmds, 1);
    CHECK_EQ(cmds[0].target, fake[1].id);
    CHECK_EQ(acts, 0);

    /* no tiles: `save all` is the hub alone */
    start(0);
    type("save all");
    run(3);
    CHECK_EQ(acts, HUB_ACT_SAVE);
    hub_save_done(&h, true);
    run(2);
    CHECK(!strcmp(text, "ok\r\n"));
}

static void bootloader_flow(void)
{
    start(2);
    settle();

    /* the tile is told; when it has answered, everybody is told to keep quiet */
    type("bootloader 0 500");
    wait_done();
    CHECK(!strcmp(text, "ok\r\n"));
    CHECK_EQ(ncmds, 1 + BCAST_REPEATS);
    CHECK_EQ(cmds[0].target, fake[0].id);
    CHECK_EQ(cmds[0].op, OP_BOOTLOADER);
    for (int i = 1; i < 1 + BCAST_REPEATS && i < ncmds; i++) {
        CHECK_EQ(cmds[i].target, ID_ALL);
        CHECK_EQ(cmds[i].op, OP_QUIET);
        CHECK_EQ(get16(cmds[i].arg), 500);
    }
    CHECK(h.node.quiet != QUIET_OFF);
    CHECK_EQ(fake[1].bcast_runs, 1);
    uint32_t from = h.node.quiet_from, until = h.node.quiet_until;
    CHECK_EQ(until - from, 500);
    CHECK(from >= cmds[BCAST_REPEATS].at && from <= cmds[1].at + QUIET_LEAD_MS);
    CHECK(now < from);
    while (now < from)
        step();
    clear();
    while (now < until - 1)
        step();
    CHECK_EQ(frames, 0);
    run(100);
    CHECK(frames > 0);
    CHECK_EQ(h.node.quiet, QUIET_OFF);

    /* how long, if nobody says */
    run(500);
    type("bootloader 1");
    wait_done();
    CHECK(!strcmp(text, "ok\r\n"));
    CHECK_EQ(get16(cmds[1].arg), HUB_QUIET_MS);
    CHECK_EQ(cmds[0].target, fake[1].id);

    /* the tile does not answer: nobody is told to keep quiet */
    start(2);
    settle();
    fake[0].deaf = 1000;
    say("bootloader 0 500");
    CHECK(!strcmp(text, "tile 0: no answer\r\nerror: 1 failed\r\n"));
    CHECK_EQ(ncmds, CMD_TRIES);
    CHECK_EQ(h.node.quiet, QUIET_OFF);
    CHECK_EQ(fake[1].bcast_runs, 0);
    fake[0].deaf = 0;

    say("bootloader");
    CHECK(!strcmp(text, "error: usage: bootloader hub|<tile> [quiet ms]\r\n"));
    say("bootloader all");
    CHECK(said("usage"));
    say("bootloader 0 0");
    CHECK(!strcmp(text, "error: quiet ms: 1 to 65535\r\n"));
    say("bootloader 0 70000");
    CHECK(said("quiet ms"));
    say("bootloader 5");
    CHECK(!strcmp(text, "error: no such tile\r\n"));
    CHECK_EQ(ncmds, 0);

    /* the hub itself: after its `ok` has had time to leave */
    type("bootloader hub");
    CHECK(!strcmp(text, "ok\r\n"));
    uint32_t t = now;
    run(ACTION_DELAY_MS - 1);
    CHECK_EQ(acts, 0);
    run(2);
    CHECK_EQ(acts, HUB_ACT_BOOTLOADER);
    CHECK_NEAR(act_at - t, ACTION_DELAY_MS, 1);

    type("reboot hub");
    CHECK(!strcmp(text, "ok\r\n"));
    run(ACTION_DELAY_MS - 1);
    CHECK_EQ(acts, 0);
    run(2);
    CHECK_EQ(acts, HUB_ACT_REBOOT);
}

static void bridge(void)
{
    start(2);
    cable(0, PORT_N, 1, PORT_S);
    settle();
    note(0, 0, true, 1000);
    drain();

    say("bridge 100");
    CHECK(!strcmp(text, "error: usage: bridge <baud> [n|e|o] [straight|swapped]\r\n"));
    say("bridge");
    CHECK(said("usage"));
    say("bridge 9600 x");
    CHECK(said("usage"));
    say("bridge 2000000");
    CHECK(said("usage"));
    CHECK(!h.bridging);
    CHECK_EQ(acts, 0);

    /* the serial port becomes a UART on the cable; the protocol stops */
    bool way = hub_swapped(&h);
    type("bridge 115200");
    CHECK(!strcmp(text, "ok\r\n"));
    CHECK(h.bridging);
    CHECK_EQ(acts, HUB_ACT_BRIDGE);
    CHECK_EQ(h.bridge.baud, 115200);
    CHECK_EQ(h.bridge.parity, 'e');
    CHECK_EQ(h.bridge.swapped, way);
    CHECK_EQ(nmidi, 1);         /* the note that sounded */
    CHECK_MIDI(0, 0x80, 48, 64);

    clear();
    uint32_t heard = T(0)->last_heard;
    run(700);
    CHECK_EQ(frames, 0);
    CHECK_EQ(hub_swapped(&h), way);     /* no searching meanwhile */
    CHECK_EQ(T(0)->last_heard, heard);  /* what comes in is not the protocol's */
    /* what is typed is for the other end, not for the console */
    CHECK_EQ(hub_console_rx(&h, (const uint8_t *)"version\n", 8, now), 8);
    run(5);
    CHECK_EQ(ntext, 0);
    CHECK_EQ(nmidi, 0);

    /* over: the console is back, and so is the protocol */
    hub_bridge_end(&h, now);
    CHECK(!h.bridging);
    run(200);
    CHECK(beacons >= 2);
    CHECK_EQ(hub_root(&h), fake[0].id);
    say("version");
    CHECK(said("piano hub"));
    say("ping 1");
    CHECK(ends("ok"));
    hub_bridge_end(&h, now);    /* twice does no harm */

    /* the options */
    type("bridge 57600 n swapped");
    CHECK_EQ(h.bridge.baud, 57600);
    CHECK_EQ(h.bridge.parity, 'n');
    CHECK(h.bridge.swapped);
    hub_bridge_end(&h, now);
    type("bridge 1200 straight o");
    CHECK_EQ(h.bridge.baud, 1200);
    CHECK_EQ(h.bridge.parity, 'o');
    CHECK(!h.bridge.swapped);
    hub_bridge_end(&h, now);

    /* A bridge inside the quiet that `bootloader` asked for (flashing a
     * tile): the bridge's end is not the quiet's. The tile's ROM is
     * listening and a HELLO would be noise to it. A second bridge, the
     * other way round, can follow. */
    start(2);
    cable(0, PORT_N, 1, PORT_S);
    settle();
    type("bootloader 0 2000");
    wait_done();
    CHECK(!strcmp(text, "ok\r\n"));
    uint32_t until = h.node.quiet_until;
    run(100);
    type("bridge 115200 e straight");
    CHECK(!strcmp(text, "ok\r\n"));
    run(300);
    clear();
    hub_bridge_end(&h, now);
    CHECK(!h.bridging);
    run(200);
    CHECK_EQ(frames, 0);
    say("version");             /* the console is back all the same */
    CHECK(said("piano hub"));
    say("ping all");
    CHECK(!strcmp(text, "error: the cable is quiet\r\n"));
    type("bridge 115200 e swapped");
    CHECK(!strcmp(text, "ok\r\n"));
    CHECK(h.bridging);
    CHECK(h.bridge.swapped);
    run(300);
    hub_bridge_end(&h, now);
    clear();
    CHECK(now < until);
    while (now < until - 1)
        step();
    CHECK_EQ(frames, 0);
    run(300);
    CHECK(beacons >= 2);
    CHECK_EQ(h.node.quiet, QUIET_OFF);
    say("ping 1");
    CHECK(ends("ok"));

    /* what follows `bridge` in the same lump of typing is thrown away too */
    const char *two = "bridge 115200\nversion\n";
    clear();
    CHECK_EQ(hub_console_rx(&h, (const uint8_t *)two, strlen(two), now), strlen(two));
    drain();
    CHECK(!strcmp(text, "ok\r\n"));
    CHECK(h.bridging);
}

/* ---------------------------------------------------------------- streams --- */

static void streams(void)
{
    char want[160];
    uint8_t raw[3 + 2 * 20];

    start(2);
    cable(0, PORT_N, 1, PORT_S);
    settle();

    say("events on");
    CHECK(!strcmp(text, "ok\r\n"));
    clear();
    note(0, 3, true, 1000);
    drain();
    snprintf(want, sizeof want, "ev 0 3 on 1000 %d %u\r\n", notes_note(&h.set, 0, 0, 3),
             notes_velocity(&h.set, 1000));
    CHECK(!strcmp(text, want));
    clear();
    note(0, 3, false, 800);
    drain();
    snprintf(want, sizeof want, "ev 0 3 off 800 %d\r\n", notes_note(&h.set, 0, 0, 3));
    CHECK(!strcmp(text, want));

    /* a key of a tile without a place: no note, but the key shows */
    fake[0].nbr_id[PORT_N] = ID_NONE;
    fake[1].nbr_id[PORT_S] = ID_NONE;
    send_info(0);
    send_info(1);
    run(2);
    clear();
    note(1, 8, true, 1000);
    note(1, 8, false, 1000);
    drain();
    snprintf(want, sizeof want, "ev 1 8 on 1000 -1 %u\r\nev 1 8 off 1000 -1\r\n",
             notes_velocity(&h.set, 1000));
    CHECK(!strcmp(text, want));
    CHECK_EQ(nmidi, 0);

    /* what a tile logs */
    uint8_t log[6] = {'h', 'e', 'l', 'l', 'o', 1};
    clear();
    up(fake[1].id, FT_LOG, log, sizeof log);
    drain();
    CHECK(!strcmp(text, "log 1 hello.\r\n"));

    say("events off");
    clear();
    note(0, 3, true, 1000);
    up(fake[1].id, FT_LOG, log, sizeof log);
    drain();
    CHECK_EQ(ntext, 0);
    say("events");
    CHECK(said("usage: events on|off"));
    say("events maybe");
    CHECK(said("usage"));

    /* one tile's ADC values */
    say("adc 1 on");
    CHECK(!strcmp(text, "ok\r\n"));
    CHECK_EQ(ncmds, 1);
    CHECK_EQ(cmds[0].target, fake[1].id);
    CHECK_EQ(cmds[0].op, OP_RAW);
    CHECK_EQ(cmds[0].arg_len, 1);
    CHECK_EQ(cmds[0].arg[0], 1);
    CHECK_EQ(h.stream_adc, fake[1].id);

    raw[0] = 7;
    raw[1] = 20;
    raw[2] = 20;
    for (int i = 0; i < 20; i++)
        put16(raw + 3 + 2 * i, (uint16_t)(2000 + i));
    clear();
    up(fake[1].id, FT_RAW, raw, sizeof raw);
    up(fake[0].id, FT_RAW, raw, sizeof raw);    /* not the tile asked about */
    drain();
    CHECK(!strcmp(text, "adc 1 7 20 2000 2001 2002 2003 2004 2005 2006 2007 2008 2009 2010 2011 "
                        "2012 2013 2014 2015 2016 2017 2018 2019\r\n"));
    /* a frame that says it holds more than it does */
    raw[2] = 21;
    clear();
    up(fake[1].id, FT_RAW, raw, sizeof raw);
    drain();
    CHECK_EQ(ntext, 0);
    raw[2] = 20;

    say("adc 1 off");
    CHECK(!strcmp(text, "ok\r\n"));
    CHECK_EQ(cmds[0].arg[0], 0);
    CHECK_EQ(h.stream_adc, 0);
    clear();
    up(fake[1].id, FT_RAW, raw, sizeof raw);
    drain();
    CHECK_EQ(ntext, 0);

    /* `adc off` goes to every tile, known or not */
    say("adc 0 on");
    say("adc off");
    CHECK(!strcmp(text, "ok\r\n"));
    CHECK_EQ(ncmds, BCAST_REPEATS);
    CHECK_EQ(cmds[0].target, ID_ALL);
    CHECK_EQ(cmds[0].op, OP_RAW);
    CHECK_EQ(cmds[0].arg[0], 0);
    CHECK_EQ(h.stream_adc, 0);

    /* a tile that does not take the command is not streamed */
    fake[0].deaf = 1000;
    say("adc 0 on");
    CHECK(ends("error: 1 failed"));
    fake[0].deaf = 0;

    say("adc");
    CHECK(said("usage: adc <tile> on|off, or adc off"));
    say("adc 0 maybe");
    CHECK(said("usage"));
    say("adc 9 on");
    CHECK(!strcmp(text, "error: no such tile\r\n"));

    /* the tile being streamed is forgotten: so is the stream */
    say("adc 1 on");
    CHECK_EQ(h.stream_adc, fake[1].id);
    fake[1].alive = false;
    run(TILE_FORGET_MS + 100);
    CHECK_EQ(h.stream_adc, 0);
}

static void power_console(void)
{
    power_in_t in;
    power_out_t out;

    start(0);
    say("power");
    CHECK(said("state: off\r\n"));
    CHECK(said("tiles: 0 mV\r\n"));
    CHECK(said("charger: 0 mV, 0 mA, asked for 15 V\r\n"));
    CHECK(said("pd chip: not found (it is powered by the charger)\r\n"));
    CHECK(said("computer: 0 mV, 0 mA, offers 500 mA (cc 0 and 0 mV), limit 0.5 A\r\n"));
    CHECK(said("usb: not configured\r\n"));
    CHECK(!said("last fault"));
    CHECK(ends("ok"));

    /* the computer feeds the tiles */
    memset(&in, 0, sizeof in);
    in.vhost_mv = 5000;
    in.usb_configured = true;
    in.cc1_mv = 900;
    for (int i = 0; i < 300; i++) {
        now++;
        in.vin_mv = out.en_host && i > 5 ? 4950 : 0;
        power_step(&h.power, &in, now, &out);
        hub_tick(&h, now);
    }
    in.ihost_ma = 420;
    power_step(&h.power, &in, now, &out);
    CHECK(out.en_host);
    say("power");
    CHECK(said("state: host\r\n"));
    CHECK(said("tiles: 4950 mV\r\n"));
    CHECK(said("computer: 5000 mV, 420 mA, offers 1500 mA (cc 900 and 0 mV), limit 1.43 A\r\n"));
    CHECK(said("usb: configured\r\n"));

    /* the PD chip, as the port's polling found it */
    h.pd.addr = 0x22;
    h.pd.status = 0x08;
    h.pd.max_ma = 3000;
    h.pd.errors = 2;
    say("power");
    CHECK(said("pd chip: at 0x22, status 0x08, charger offers 3000 mA, 2 errors\r\n"));

    say("power off");
    CHECK(!strcmp(text, "ok\r\n"));
    power_step(&h.power, &in, ++now, &out);
    CHECK(!out.en_host && !out.en_pd);
    say("power");
    CHECK(said("state: manual off\r\n"));
    say("power auto");
    power_step(&h.power, &in, ++now, &out);
    CHECK(out.en_host);
    say("power retry");
    CHECK(!strcmp(text, "ok\r\n"));
    say("power maybe");
    CHECK(!strcmp(text, "error: usage: power [auto|off|retry]\r\n"));

    /* a short: the fault shows */
    in.vin_mv = 300;
    for (int i = 0; i < 200; i++)
        power_step(&h.power, &in, ++now, &out);
    say("power");
    CHECK(said("state: retry\r\n"));
    CHECK(said("last fault: output low on the computer path, 1 in a row\r\n"));
}

/* Nobody takes the MIDI or reads the console: the oldest is kept, the rest
 * counted. */
static void overflow(void)
{
    start(1);
    settle();
    hold_midi = true;
    for (int i = 0; i < 200; i++) {
        note(0, 0, true, 1000);
        note(0, 0, false, 1000);
    }
    CHECK_EQ(h.midi_count, HUB_MIDI_LEN);
    CHECK_EQ(h.stats.midi_lost, 400 - HUB_MIDI_LEN);
    hold_midi = false;
    drain();
    CHECK_EQ(nmidi, HUB_MIDI_LEN);
    CHECK_MIDI(0, 0x90, 48, notes_velocity(&h.set, 1000));
    CHECK_MIDI(HUB_MIDI_LEN - 1, 0x80, 48, notes_velocity(&h.set, 1000));
    clear();
    note(0, 0, true, 1000);     /* and it carries on */
    drain();
    CHECK_EQ(nmidi, 1);

    hold_text = true;
    for (int i = 0; i < 10; i++) {
        hub_console_rx(&h, (const uint8_t *)"help\n", 5, now);
        step();
    }
    CHECK_EQ(h.out_count, HUB_OUT_LEN);
    CHECK(h.stats.out_lost > 0);
    hold_text = false;
    clear();
    drain();
    CHECK_EQ(ntext, HUB_OUT_LEN);
    CHECK(!strncmp(text, "tiles | grid", 12));
    say("stats");
    CHECK(said(" characters dropped\r\n"));
    CHECK(ends("ok"));
}

int main(void)
{
    RUN(table);
    RUN(grid_line);
    RUN(grid_3x3);
    RUN(grid_problems);
    RUN(notes);
    RUN(same_pitch);
    RUN(state_heals);
    RUN(aftertouch);
    RUN(console_basics);
    RUN(settings);
    RUN(commands);
    RUN(counters);
    RUN(broadcast);
    RUN(save);
    RUN(bootloader_flow);
    RUN(bridge);
    RUN(streams);
    RUN(power_console);
    RUN(overflow);
    return T_DONE();
}
