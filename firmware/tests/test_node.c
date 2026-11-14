/* node.c: the beacon tree, forwarding, queues, quiet. One node at a time,
 * fed by hand; whole networks are in the scenario tests. */
#include "node.h"
#include "t.h"

#define HUB   0x48554231u
#define HUB2  0x48554232u
#define ME    0x000000A1u
#define OTHER 0x000000B2u

/* ---- helpers ---- */

static struct {
    int count;
    uint8_t port, type, len;
    uint8_t pl[FRAME_MAX_PAYLOAD];
} got;

static void deliver(void *ctx, uint8_t port, uint8_t type, const uint8_t *pl, uint8_t len,
                    uint32_t now)
{
    (void)ctx;
    (void)now;
    got.count++;
    got.port = port;
    got.type = type;
    got.len = len;
    memcpy(got.pl, pl, len);
}

static void inject(node_t *n, uint8_t port, uint8_t type, const uint8_t *pl, uint8_t len,
                   uint32_t now)
{
    uint8_t wire[FRAME_MAX_WIRE];
    size_t w = frame_encode(type, pl, len, wire);

    node_rx(n, port, wire, w, now);
}

static void hello(node_t *n, uint8_t port, uint32_t id, uint8_t their_port, uint32_t now)
{
    uint8_t pl[HELLO_LEN];

    link_hello_payload(pl, KIND_TILE, id, their_port);
    inject(n, port, FT_HELLO, pl, HELLO_LEN, now);
}

static void beacon(node_t *n, uint8_t port, uint32_t hub, uint16_t seq, uint8_t hops, uint32_t now)
{
    uint8_t pl[BEACON_LEN];

    put32(pl, hub);
    put16(pl + 4, seq);
    pl[6] = hops;
    pl[7] = 0;
    inject(n, port, FT_BEACON, pl, BEACON_LEN, now);
}

/* An upstream frame written by tile `id`. */
static void upstream(node_t *n, uint8_t port, uint8_t type, uint32_t id, uint8_t tag, uint32_t now)
{
    uint8_t pl[8];

    put32(pl, id);
    pl[4] = tag;
    inject(n, port, type, pl, 5, now);
}

static void cmd(node_t *n, uint8_t port, uint32_t target, uint8_t counter, uint32_t now)
{
    uint8_t pl[CMD_HEAD_LEN];

    put32(pl, target);
    pl[4] = counter;
    pl[5] = OP_PING;
    inject(n, port, FT_CMD, pl, CMD_HEAD_LEN, now);
}

/* The next frame the node wants to send on a port, decoded. */
static bool next(node_t *n, uint8_t port, uint8_t *type, uint8_t *pl, uint8_t *len)
{
    uint8_t wire[FRAME_MAX_WIRE];
    frame_rx_t rx;
    size_t w = node_tx(n, port, wire);

    if (w == 0)
        return false;
    frame_rx_reset(&rx);
    for (size_t i = 0; i < w; i++) {
        frame_result_t r = frame_rx_byte(&rx, wire[i], type, pl, len);
        if (i + 1 == w)
            CHECK_EQ(r, FRAME_OK);
        else
            CHECK_EQ(r, FRAME_NONE);
    }
    return true;
}

/* The same, passing over HELLOs. */
static bool next_data(node_t *n, uint8_t port, uint8_t *type, uint8_t *pl, uint8_t *len)
{
    while (next(n, port, type, pl, len)) {
        if (*type != FT_HELLO)
            return true;
    }
    return false;
}

static void drain(node_t *n)
{
    uint8_t type, len, pl[FRAME_MAX_PAYLOAD];

    for (uint8_t p = 0; p < n->nports; p++) {
        while (next(n, p, &type, pl, &len))
            ;
    }
}

/* A tile whose four ports all have a neighbour and nothing left to send. */
static void fresh_tile(node_t *n, uint32_t now)
{
    memset(&got, 0, sizeof got);
    node_init(n, KIND_TILE, TILE_PORTS, ME, deliver, NULL, now);
    for (uint8_t p = 0; p < TILE_PORTS; p++)
        hello(n, p, 0x1000u + p, (uint8_t)((p + 2) % 4), now);
    drain(n);
    n->changed = false;
}

/* ---- tests ---- */

static void hub_sends_beacons(void)
{
    node_t n;
    uint8_t type, len, pl[FRAME_MAX_PAYLOAD];
    int beacons = 0;
    uint32_t first = 0, last = 0;
    uint16_t seq = 0;

    node_init(&n, KIND_HUB, 1, HUB, deliver, NULL, 0);
    for (uint32_t t = 1; t <= 2000; t++) {
        node_tick(&n, t);
        while (next(&n, 0, &type, pl, &len)) {
            if (type != FT_BEACON)
                continue;
            CHECK_EQ(len, BEACON_LEN);
            CHECK_EQ(get32(pl), HUB);
            CHECK_EQ(pl[6], 0);
            if (beacons == 0)
                first = t;
            else {
                CHECK_EQ(get16(pl + 4), (uint16_t)(seq + 1));
                CHECK_EQ(t - last, BEACON_PERIOD_MS);
            }
            seq = get16(pl + 4);
            last = t;
            beacons++;
        }
    }
    CHECK_EQ(first, HUB_START_MS);
    CHECK_EQ(beacons, (2000 - HUB_START_MS) / BEACON_PERIOD_MS + 1);
}

static void tile_takes_a_parent(void)
{
    node_t n;
    uint8_t type, len, pl[FRAME_MAX_PAYLOAD];

    fresh_tile(&n, 0);
    CHECK_EQ(n.parent, PORT_NONE);

    beacon(&n, PORT_E, HUB, 10, 0, 5);
    CHECK_EQ(n.parent, PORT_E);
    CHECK_EQ(n.parent_hops, 0);
    CHECK_EQ(n.hub_id, HUB);
    CHECK(n.changed);

    /* forwarded on the three other ports with one more hop, not back */
    for (uint8_t p = 0; p < TILE_PORTS; p++) {
        bool any = next_data(&n, p, &type, pl, &len);
        CHECK_EQ(any, p != PORT_E);
        if (any) {
            CHECK_EQ(type, FT_BEACON);
            CHECK_EQ(get32(pl), HUB);
            CHECK_EQ(get16(pl + 4), 10);
            CHECK_EQ(pl[6], 1);
        }
        CHECK(!next_data(&n, p, &type, pl, &len));
    }

    /* the same beacon again, or an older one: nothing */
    beacon(&n, PORT_E, HUB, 10, 0, 6);
    beacon(&n, PORT_E, HUB, 9, 0, 7);
    for (uint8_t p = 0; p < TILE_PORTS; p++)
        CHECK(!next_data(&n, p, &type, pl, &len));
}

static void parent_rules(void)
{
    node_t n;
    uint8_t type, len, pl[FRAME_MAX_PAYLOAD];

    fresh_tile(&n, 0);
    beacon(&n, PORT_E, HUB, 10, 2, 5);
    drain(&n);
    CHECK_EQ(n.parent, PORT_E);

    /* a newer beacon from elsewhere, but no shorter: remembered, not followed */
    beacon(&n, PORT_N, HUB, 11, 2, 6);
    CHECK_EQ(n.parent, PORT_E);
    CHECK_EQ(n.port[PORT_N].hops, 2);
    beacon(&n, PORT_S, HUB, 11, 5, 6);
    CHECK_EQ(n.parent, PORT_E);
    for (uint8_t p = 0; p < TILE_PORTS; p++)
        CHECK(!next_data(&n, p, &type, pl, &len));

    /* newer and shorter: that port is the parent now */
    n.changed = false;
    beacon(&n, PORT_W, HUB, 11, 1, 7);
    CHECK_EQ(n.parent, PORT_W);
    CHECK_EQ(n.parent_hops, 1);
    CHECK(n.changed);
    CHECK(!next_data(&n, PORT_W, &type, pl, &len));
    CHECK(next_data(&n, PORT_E, &type, pl, &len));
    CHECK_EQ(pl[6], 2);
    drain(&n);

    /* the old parent offering what we already forwarded: nothing */
    beacon(&n, PORT_E, HUB, 11, 2, 8);
    CHECK_EQ(n.parent, PORT_W);
    CHECK(!next_data(&n, PORT_N, &type, pl, &len));

    /* sequence numbers wrap: "newer" means less than half way round ahead */
    beacon(&n, PORT_W, HUB, 0x7000, 1, 9);
    beacon(&n, PORT_W, HUB, 0xE000, 1, 9);
    beacon(&n, PORT_W, HUB, 0xFFFF, 1, 9);
    CHECK_EQ(n.last_seq, 0xFFFF);
    drain(&n);
    beacon(&n, PORT_W, HUB, 0, 1, 10);
    CHECK(next_data(&n, PORT_E, &type, pl, &len));
    CHECK_EQ(get16(pl + 4), 0);
    drain(&n);
    beacon(&n, PORT_W, HUB, 0xFFFF, 1, 11);     /* older again */
    CHECK(!next_data(&n, PORT_N, &type, pl, &len));
}

/* Only the newest beacon waits on a port, however slowly it is sent. */
static void newest_beacon_only(void)
{
    node_t n;
    uint8_t type, len, pl[FRAME_MAX_PAYLOAD];

    fresh_tile(&n, 0);
    for (uint16_t s = 1; s <= 5; s++)
        beacon(&n, PORT_E, HUB, s, 0, 5 + s);
    CHECK(next_data(&n, PORT_W, &type, pl, &len));
    CHECK_EQ(get16(pl + 4), 5);
    CHECK(!next_data(&n, PORT_W, &type, pl, &len));
}

static void parent_lost_and_forgotten(void)
{
    node_t n;
    uint32_t t = 0;

    fresh_tile(&n, 0);
    beacon(&n, PORT_E, HUB, 100, 0, 0);
    uint8_t epoch = n.epoch;

    for (t = 1; t < PARENT_TIMEOUT_MS; t++) {
        node_tick(&n, t);
        CHECK_EQ(n.parent, PORT_E);
    }
    n.changed = false;
    node_tick(&n, t);
    CHECK_EQ(n.parent, PORT_NONE);
    CHECK(n.changed);

    /* the hub is remembered for a while: its old numbers stay old */
    beacon(&n, PORT_N, HUB, 90, 0, t + 1);
    CHECK_EQ(n.parent, PORT_NONE);
    CHECK_EQ(n.epoch, epoch);

    for (t = t + 1; t < PARENT_TIMEOUT_MS + SEQ_FORGET_MS; t++)
        node_tick(&n, t);
    CHECK_EQ(n.epoch, epoch);
    node_tick(&n, t);
    CHECK_EQ(n.epoch, (uint8_t)(epoch + 1));
    CHECK_EQ(n.hub_id, ID_NONE);

    /* a hub that started again, counting from 0, is believed now */
    beacon(&n, PORT_N, HUB, 0, 0, t + 1);
    CHECK_EQ(n.parent, PORT_N);
    /* forgetting happens once per loss */
    CHECK_EQ(n.epoch, (uint8_t)(epoch + 1));
}

static void one_hub_at_a_time(void)
{
    node_t n;
    uint8_t type, len, pl[FRAME_MAX_PAYLOAD];

    fresh_tile(&n, 0);
    beacon(&n, PORT_E, HUB, 10, 3, 1);
    drain(&n);

    /* another hub, nearer, newer: ignored while the first is there */
    beacon(&n, PORT_W, HUB2, 500, 0, 2);
    CHECK_EQ(n.parent, PORT_E);
    CHECK_EQ(n.hub_id, HUB);
    CHECK(!next_data(&n, PORT_N, &type, pl, &len));

    /* the first hub goes away: the other one is taken at once, with its
     * own numbering */
    for (uint32_t t = 2; t <= 2 + PARENT_TIMEOUT_MS; t++)
        node_tick(&n, t);
    CHECK_EQ(n.parent, PORT_NONE);
    beacon(&n, PORT_W, HUB2, 3, 0, 300);
    CHECK_EQ(n.parent, PORT_W);
    CHECK_EQ(n.hub_id, HUB2);
}

static void upstream_goes_to_the_parent(void)
{
    node_t n;
    uint8_t type, len, pl[FRAME_MAX_PAYLOAD];

    fresh_tile(&n, 0);

    /* no parent: dropped */
    upstream(&n, PORT_W, FT_NOTE, OTHER, 1, 1);
    for (uint8_t p = 0; p < TILE_PORTS; p++)
        CHECK_EQ(node_queued(&n, p), 0);
    CHECK(!node_send_up(&n, FT_NOTE, (const uint8_t *)"x", 1));

    beacon(&n, PORT_E, HUB, 10, 0, 2);
    drain(&n);

    /* from a child: on to the parent, unchanged */
    upstream(&n, PORT_W, FT_NOTE, OTHER, 7, 3);
    CHECK_EQ(node_queued(&n, PORT_E), 1);
    CHECK(next_data(&n, PORT_E, &type, pl, &len));
    CHECK_EQ(type, FT_NOTE);
    CHECK_EQ(len, 5);
    CHECK_EQ(get32(pl), OTHER);
    CHECK_EQ(pl[4], 7);

    /* from the parent's side: wrong way, dropped */
    upstream(&n, PORT_E, FT_NOTE, OTHER, 8, 4);
    /* our own, come back round a loop: dropped */
    upstream(&n, PORT_N, FT_NOTE, ME, 9, 4);
    /* too short to carry an ID: dropped */
    inject(&n, PORT_N, FT_NOTE, (const uint8_t *)"ab", 2, 4);
    for (uint8_t p = 0; p < TILE_PORTS; p++)
        CHECK_EQ(node_queued(&n, p), 0);

    /* our own frames get our ID in front */
    CHECK(node_send_up(&n, FT_STATE, (const uint8_t *)"\x05\x06", 2));
    CHECK(next_data(&n, PORT_E, &type, pl, &len));
    CHECK_EQ(type, FT_STATE);
    CHECK_EQ(len, 6);
    CHECK_EQ(get32(pl), ME);
    CHECK_EQ(pl[5], 6);

    /* a type nobody has invented yet is forwarded all the same */
    upstream(&n, PORT_S, 0x2F, OTHER, 1, 5);
    CHECK(next_data(&n, PORT_E, &type, pl, &len));
    CHECK_EQ(type, 0x2F);

    /* nothing ever went out of a child port */
    for (uint8_t p = 0; p < TILE_PORTS; p++)
        CHECK(!next_data(&n, p, &type, pl, &len));
    CHECK_EQ(got.count, 0);                     /* and none of it was for the tile itself */
}

static void commands_go_down_the_tree(void)
{
    node_t n;
    uint8_t type, len, pl[FRAME_MAX_PAYLOAD];

    /* only W and E have neighbours here */
    memset(&got, 0, sizeof got);
    node_init(&n, KIND_TILE, TILE_PORTS, ME, deliver, NULL, 0);
    hello(&n, PORT_W, 0x1000, PORT_E, 0);
    hello(&n, PORT_E, 0x1002, PORT_W, 0);
    beacon(&n, PORT_E, HUB, 1, 0, 1);
    drain(&n);

    /* from a port that is not the parent: ignored altogether */
    cmd(&n, PORT_W, ME, 1, 2);
    CHECK_EQ(got.count, 0);
    for (uint8_t p = 0; p < TILE_PORTS; p++)
        CHECK(!next_data(&n, p, &type, pl, &len));

    /* from the parent, for us: run here, and passed on where somebody is */
    cmd(&n, PORT_E, ME, 2, 3);
    CHECK_EQ(got.count, 1);
    CHECK_EQ(got.type, FT_CMD);
    CHECK_EQ(got.pl[4], 2);
    CHECK(next_data(&n, PORT_W, &type, pl, &len));
    CHECK_EQ(type, FT_CMD);
    CHECK_EQ(get32(pl), ME);
    CHECK(!next_data(&n, PORT_N, &type, pl, &len));
    CHECK(!next_data(&n, PORT_S, &type, pl, &len));
    CHECK(!next_data(&n, PORT_E, &type, pl, &len));

    /* for everybody */
    cmd(&n, PORT_E, ID_ALL, 3, 4);
    CHECK_EQ(got.count, 2);
    CHECK(next_data(&n, PORT_W, &type, pl, &len));

    /* for somebody else: only passed on */
    cmd(&n, PORT_E, OTHER, 4, 5);
    CHECK_EQ(got.count, 2);
    CHECK(next_data(&n, PORT_W, &type, pl, &len));
    CHECK_EQ(get32(pl), OTHER);
}

static void hello_then_beacon_then_queue(void)
{
    node_t n;
    uint8_t type, len, pl[FRAME_MAX_PAYLOAD];

    fresh_tile(&n, 0);
    beacon(&n, PORT_E, HUB, 1, 0, 1);
    drain(&n);

    /* queue three upstream frames toward E, a command toward W, then make a
     * beacon and a HELLO due on both */
    for (uint8_t i = 0; i < 3; i++)
        upstream(&n, PORT_W, FT_NOTE, OTHER, i, 2);
    cmd(&n, PORT_E, OTHER, 1, 2);
    beacon(&n, PORT_E, HUB, 2, 0, 3);
    for (uint32_t t = 3; t <= 3 + HELLO_PERIOD_MS; t++) {
        beacon(&n, PORT_E, HUB, (uint16_t)t, 0, t);     /* keep the parent */
        node_tick(&n, t);
    }

    CHECK(next(&n, PORT_E, &type, pl, &len));
    CHECK_EQ(type, FT_HELLO);
    CHECK_EQ(get32(pl + 2), ME);
    CHECK_EQ(pl[6], PORT_E);
    for (uint8_t i = 0; i < 3; i++) {
        CHECK(next(&n, PORT_E, &type, pl, &len));
        CHECK_EQ(type, FT_NOTE);
        CHECK_EQ(pl[4], i);                     /* in the order they came */
    }
    CHECK(!next(&n, PORT_E, &type, pl, &len));

    CHECK(next(&n, PORT_W, &type, pl, &len));
    CHECK_EQ(type, FT_HELLO);
    CHECK(next(&n, PORT_W, &type, pl, &len));
    CHECK_EQ(type, FT_BEACON);
    CHECK(next(&n, PORT_W, &type, pl, &len));
    CHECK_EQ(type, FT_CMD);
    CHECK(!next(&n, PORT_W, &type, pl, &len));
}

/* What is left in the parent's queue, as a string of letters by type. */
static void queue_types(node_t *n, char *out)
{
    uint8_t type, len, pl[FRAME_MAX_PAYLOAD];
    size_t k = 0;

    while (next_data(n, PORT_E, &type, pl, &len)) {
        out[k++] = type == FT_RAW ? 'R' : type == FT_LOG ? 'L' : type == FT_POS ? 'P' :
                   type == FT_INFO ? 'I' : type == FT_STATE ? 'S' : type == FT_NOTE ? 'N' :
                   type == FT_REPLY ? 'Y' : '?';
        out[k++] = (char)('0' + pl[4] % 10);
    }
    out[k] = 0;
}

static void full_queue_drops_the_least_important(void)
{
    node_t n;
    char left[4 * NODE_QUEUE_LEN];

    fresh_tile(&n, 0);
    beacon(&n, PORT_E, HUB, 1, 0, 1);
    drain(&n);

    /* fill it: 8 RAW, 4 LOG, 4 POS, 4 STATE, 4 NOTE = 24 */
    for (uint8_t i = 0; i < 8; i++)
        upstream(&n, PORT_W, FT_RAW, OTHER, i, 2);
    for (uint8_t i = 0; i < 4; i++)
        upstream(&n, PORT_W, FT_LOG, OTHER, i, 2);
    for (uint8_t i = 0; i < 4; i++)
        upstream(&n, PORT_W, FT_POS, OTHER, i, 2);
    for (uint8_t i = 0; i < 4; i++)
        upstream(&n, PORT_W, FT_STATE, OTHER, i, 2);
    for (uint8_t i = 0; i < 4; i++)
        upstream(&n, PORT_W, FT_NOTE, OTHER, i, 2);
    CHECK_EQ(node_queued(&n, PORT_E), NODE_QUEUE_LEN);
    CHECK_EQ(n.queue_drops, 0);

    /* one more RAW pushes out the oldest RAW */
    upstream(&n, PORT_W, FT_RAW, OTHER, 8, 3);
    CHECK_EQ(n.queue_drops, 1);
    /* 12 NOTEs push out the 8 RAW (numbers 1 to 8) and the 4 LOG */
    for (uint8_t i = 4; i < 16; i++)
        upstream(&n, PORT_W, FT_NOTE, OTHER, i, 3);
    CHECK_EQ(n.queue_drops, 13);
    CHECK_EQ(node_queued(&n, PORT_E), NODE_QUEUE_LEN);
    /* a RAW now finds nothing less important than itself: POS, STATE and
     * NOTE all outrank it, so it is the one dropped */
    upstream(&n, PORT_W, FT_RAW, OTHER, 9, 3);
    CHECK_EQ(n.queue_drops, 14);
    /* an INFO pushes out the oldest POS; a REPLY the next one */
    upstream(&n, PORT_W, FT_INFO, OTHER, 0, 3);
    upstream(&n, PORT_W, FT_REPLY, OTHER, 0, 3);
    CHECK_EQ(n.queue_drops, 16);

    queue_types(&n, left);
    CHECK(strcmp(left, "P2P3S0S1S2S3N0N1N2N3N4N5N6N7N8N9N0N1N2N3N4N5I0Y0") == 0);
    if (strcmp(left, "P2P3S0S1S2S3N0N1N2N3N4N5N6N7N8N9N0N1N2N3N4N5I0Y0") != 0)
        printf("  queue was: %s\n", left);

    /* all NOTEs: a POS, a STATE, an INFO are dropped; a NOTE replaces the
     * oldest NOTE */
    for (uint8_t i = 0; i < NODE_QUEUE_LEN; i++)
        upstream(&n, PORT_W, FT_NOTE, OTHER, (uint8_t)(i % 10), 4);
    uint16_t before = n.queue_drops;
    upstream(&n, PORT_W, FT_POS, OTHER, 0, 5);
    upstream(&n, PORT_W, FT_STATE, OTHER, 0, 5);
    upstream(&n, PORT_W, FT_INFO, OTHER, 0, 5);
    upstream(&n, PORT_W, FT_NOTE, OTHER, 4, 5);
    CHECK_EQ(n.queue_drops, before + 4);
    queue_types(&n, left);
    CHECK(strcmp(left, "N1N2N3N4N5N6N7N8N9N0N1N2N3N4N5N6N7N8N9N0N1N2N3N4") == 0);
}

/* Commands share a child port's queue with nothing else that matters, but
 * they must never give way to upstream frames. */
static void commands_are_not_dropped(void)
{
    node_t n;
    uint8_t type, len, pl[FRAME_MAX_PAYLOAD];
    int cmds = 0;

    fresh_tile(&n, 0);
    beacon(&n, PORT_E, HUB, 1, 0, 1);
    drain(&n);
    for (uint8_t i = 0; i < NODE_QUEUE_LEN + 6; i++)
        cmd(&n, PORT_E, OTHER, i, 2);
    CHECK_EQ(node_queued(&n, PORT_W), NODE_QUEUE_LEN);
    CHECK_EQ(n.queue_drops, 6 * 3);             /* on each of the three child ports */
    while (next_data(&n, PORT_W, &type, pl, &len)) {
        CHECK_EQ(type, FT_CMD);
        CHECK_EQ(pl[4], cmds + 6);              /* the oldest six went */
        cmds++;
    }
    CHECK_EQ(cmds, NODE_QUEUE_LEN);
}

static void queue_follows_the_parent(void)
{
    node_t n;
    uint8_t type, len, pl[FRAME_MAX_PAYLOAD];

    fresh_tile(&n, 0);
    beacon(&n, PORT_E, HUB, 1, 3, 1);
    drain(&n);
    upstream(&n, PORT_S, FT_NOTE, OTHER, 1, 2);
    upstream(&n, PORT_S, FT_NOTE, OTHER, 2, 2);
    CHECK_EQ(node_queued(&n, PORT_E), 2);

    /* a shorter way appears: what was waiting goes that way, in order */
    beacon(&n, PORT_W, HUB, 2, 1, 3);
    CHECK_EQ(node_queued(&n, PORT_E), 0);
    CHECK_EQ(node_queued(&n, PORT_W), 2);
    CHECK(next_data(&n, PORT_W, &type, pl, &len));
    CHECK_EQ(pl[4], 1);
    CHECK(next_data(&n, PORT_W, &type, pl, &len));
    CHECK_EQ(pl[4], 2);

    /* the parent goes: what waits for it is dropped */
    upstream(&n, PORT_S, FT_NOTE, OTHER, 3, 4);
    CHECK_EQ(node_queued(&n, PORT_W), 1);
    for (uint32_t t = 4; t <= 4 + PARENT_TIMEOUT_MS; t++)
        node_tick(&n, t);
    CHECK_EQ(n.parent, PORT_NONE);
    CHECK_EQ(node_queued(&n, PORT_W), 0);
}

static void quiet(void)
{
    node_t n;
    uint8_t type, len, pl[FRAME_MAX_PAYLOAD];
    uint8_t wire[FRAME_MAX_WIRE];
    uint32_t t;

    fresh_tile(&n, 0);
    beacon(&n, PORT_E, HUB, 1, 0, 1);
    drain(&n);

    node_quiet(&n, 300, 10);
    /* during the lead time everything still works */
    upstream(&n, PORT_W, FT_REPLY, OTHER, 1, 11);
    CHECK(next_data(&n, PORT_E, &type, pl, &len));
    CHECK_EQ(type, FT_REPLY);

    upstream(&n, PORT_W, FT_NOTE, OTHER, 2, 12);
    for (t = 11; t < 10 + QUIET_LEAD_MS; t++) {
        node_tick(&n, t);
        CHECK(n.quiet != QUIET_ON);
    }
    node_tick(&n, t);
    CHECK_EQ(n.quiet, QUIET_ON);

    /* silent: nothing to send on any port, whatever was queued or is due,
     * and deaf */
    for (; t < 10 + QUIET_LEAD_MS + 300; t++) {
        node_tick(&n, t);
        beacon(&n, PORT_E, HUB, (uint16_t)t, 0, t);
        for (uint8_t p = 0; p < TILE_PORTS; p++)
            CHECK_EQ(node_tx(&n, p, wire), 0);
        CHECK_EQ(n.parent, PORT_NONE);
        CHECK(!node_send_up(&n, FT_NOTE, (const uint8_t *)"x", 1));
    }

    /* over: links start again, the way round they were */
    bool was_swapped = node_swapped(&n, PORT_E);
    node_tick(&n, t);
    CHECK_EQ(n.quiet, QUIET_OFF);
    CHECK_EQ(node_swapped(&n, PORT_E), was_swapped);
    CHECK(next(&n, PORT_E, &type, pl, &len));
    CHECK_EQ(type, FT_HELLO);
    beacon(&n, PORT_E, HUB, (uint16_t)(t + 5), 0, t + 1);
    CHECK_EQ(n.parent, PORT_E);
}

static void pause_and_resume(void)
{
    node_t n;
    uint8_t wire[FRAME_MAX_WIRE];
    uint8_t type, len, pl[FRAME_MAX_PAYLOAD];

    memset(&got, 0, sizeof got);
    node_init(&n, KIND_HUB, 1, HUB, deliver, NULL, 0);
    for (uint32_t t = 1; t < 1000; t++)
        node_tick(&n, t);
    node_pause(&n, 1000);
    for (uint32_t t = 1000; t < 100000; t += 7) {
        node_tick(&n, t);
        CHECK_EQ(node_tx(&n, 0, wire), 0);
    }
    upstream(&n, 0, FT_NOTE, OTHER, 1, 100000);
    CHECK_EQ(got.count, 0);
    CHECK(!node_send_down(&n, FT_CMD, (const uint8_t *)"abcdef", 6));

    node_resume(&n, 100000);
    CHECK(next(&n, 0, &type, pl, &len));
    CHECK_EQ(type, FT_HELLO);
    bool beaconed = false;
    for (uint32_t t = 100001; t < 100100; t++) {
        node_tick(&n, t);
        while (next(&n, 0, &type, pl, &len))
            beaconed |= type == FT_BEACON;
    }
    CHECK(beaconed);
}

/* The hub's bridge inside a QUIET (flashing a tile): when the bridge ends
 * and the quiet has not, the node keeps silent to the quiet's end. */
static void pause_inside_a_quiet(void)
{
    node_t n;
    uint8_t wire[FRAME_MAX_WIRE];
    uint8_t type, len, pl[FRAME_MAX_PAYLOAD];
    uint32_t t;

    memset(&got, 0, sizeof got);
    node_init(&n, KIND_HUB, 1, HUB, deliver, NULL, 0);
    for (t = 1; t < 1000; t++)
        node_tick(&n, t);
    drain(&n);

    /* paused in the quiet's lead time, resumed in the middle of it */
    node_quiet(&n, 2000, 1000);
    const uint32_t end = 1000 + QUIET_LEAD_MS + 2000;
    node_pause(&n, 1005);
    for (t = 1005; t < 1500; t++) {
        node_tick(&n, t);
        CHECK_EQ(node_tx(&n, 0, wire), 0);
    }
    node_resume(&n, 1500);
    CHECK_EQ(n.quiet, QUIET_ON);
    CHECK(!n.quiet_forever);

    /* paused and resumed once more: the same */
    node_pause(&n, 1600);
    CHECK(n.quiet_forever);
    node_resume(&n, 1700);
    CHECK_EQ(n.quiet, QUIET_ON);

    for (t = 1700; t < end; t++) {
        node_tick(&n, t);
        CHECK_EQ(node_tx(&n, 0, wire), 0);
        upstream(&n, 0, FT_NOTE, OTHER, 1, t);
    }
    CHECK_EQ(got.count, 0);
    node_tick(&n, end);
    CHECK_EQ(n.quiet, QUIET_OFF);
    CHECK(next(&n, 0, &type, pl, &len));
    CHECK_EQ(type, FT_HELLO);

    /* a pause that outlasts the quiet: resuming is at once */
    for (t = end; t < 5000; t++)
        node_tick(&n, t);
    drain(&n);
    node_quiet(&n, 300, 5000);
    for (t = 5000; t < 5100; t++)
        node_tick(&n, t);
    CHECK_EQ(n.quiet, QUIET_ON);
    node_pause(&n, 5100);
    for (t = 5100; t < 6000; t++) {
        node_tick(&n, t);
        CHECK_EQ(node_tx(&n, 0, wire), 0);
    }
    CHECK(!n.quiet_under);              /* forgotten when its time was up */
    node_resume(&n, 6000);
    CHECK_EQ(n.quiet, QUIET_OFF);
    CHECK(next(&n, 0, &type, pl, &len));
    CHECK_EQ(type, FT_HELLO);

    /* a pause with no quiet under it, after all that: at once too */
    node_pause(&n, 6100);
    CHECK(!n.quiet_under);
    node_resume(&n, 6200);
    CHECK_EQ(n.quiet, QUIET_OFF);

    /* a QUIET that arrives during a pause changes nothing */
    node_pause(&n, 6300);
    node_quiet(&n, 60000, 6310);
    CHECK(n.quiet_forever);
    node_resume(&n, 6400);
    CHECK_EQ(n.quiet, QUIET_OFF);
}

/* Half a frame, a pause, then a whole frame: the half is thrown away
 * instead of spoiling the frame behind it. */
static void stalled_frame_is_dropped(void)
{
    node_t n;
    uint8_t wire[FRAME_MAX_WIRE], pl[BEACON_LEN];
    size_t w;

    fresh_tile(&n, 0);
    put32(pl, HUB);
    put16(pl + 4, 1);
    pl[6] = 0;
    pl[7] = 0;
    w = frame_encode(FT_BEACON, pl, BEACON_LEN, wire);

    node_rx(&n, PORT_E, wire, w / 2, 10);
    for (uint32_t t = 10; t <= 10 + RX_GAP_MS; t++)
        node_tick(&n, t);
    node_rx(&n, PORT_E, wire, w, 20);
    CHECK_EQ(n.parent, PORT_E);
    CHECK_EQ(n.crc_drops, 0);

    /* without the pause the two run together and are one bad frame */
    put16(pl + 4, 2);
    w = frame_encode(FT_BEACON, pl, BEACON_LEN, wire);
    node_rx(&n, PORT_E, wire, w / 2, 30);
    node_tick(&n, 31);
    node_rx(&n, PORT_E, wire, w, 31);
    CHECK_EQ(n.crc_drops, 1);
    CHECK_EQ(n.last_seq, 1);
}

static void bad_frames_are_counted(void)
{
    node_t n;
    uint8_t wire[FRAME_MAX_WIRE], pl[BEACON_LEN] = {0};
    size_t w;

    fresh_tile(&n, 0);
    put32(pl, HUB);
    w = frame_encode(FT_BEACON, pl, BEACON_LEN, wire);
    wire[3] ^= 0x10;
    node_rx(&n, PORT_E, wire, w, 1);
    node_rx(&n, PORT_E, wire, w, 1);
    CHECK_EQ(n.crc_drops, 2);
    CHECK_EQ(n.parent, PORT_NONE);
    /* a bad frame does not keep a link up */
    CHECK(n.port[PORT_E].link.last_valid == 0);
}

static void hub_listens_to_tiles_only(void)
{
    node_t n;

    memset(&got, 0, sizeof got);
    node_init(&n, KIND_HUB, 1, HUB, deliver, NULL, 0);
    hello(&n, 0, ME, PORT_E, 1);
    CHECK_EQ(n.port[0].link.nbr_id, ME);
    CHECK_EQ(n.port[0].link.nbr_port, PORT_E);

    upstream(&n, 0, FT_NOTE, ME, 3, 2);
    CHECK_EQ(got.count, 1);
    CHECK_EQ(got.type, FT_NOTE);
    CHECK_EQ(get32(got.pl), ME);

    beacon(&n, 0, HUB2, 1, 0, 3);
    cmd(&n, 0, HUB, 1, 3);
    CHECK_EQ(got.count, 1);
    CHECK_EQ(node_queued(&n, 0), 0);

    CHECK(node_send_down(&n, FT_CMD, (const uint8_t *)"abcdef", 6));
    CHECK_EQ(node_queued(&n, 0), 1);
}

/* A cable from one of a tile's ports to another of its own. */
static void own_hello_is_nobody(void)
{
    node_t n;

    node_init(&n, KIND_TILE, TILE_PORTS, ME, deliver, NULL, 0);
    hello(&n, PORT_W, ME, PORT_E, 1);
    CHECK_EQ(n.port[PORT_W].link.nbr_id, ID_NONE);
    CHECK(n.port[PORT_W].link.up);              /* the cable works; there is just nobody on it */
}

int main(void)
{
    RUN(hub_sends_beacons);
    RUN(tile_takes_a_parent);
    RUN(parent_rules);
    RUN(newest_beacon_only);
    RUN(parent_lost_and_forgotten);
    RUN(one_hub_at_a_time);
    RUN(upstream_goes_to_the_parent);
    RUN(commands_go_down_the_tree);
    RUN(hello_then_beacon_then_queue);
    RUN(full_queue_drops_the_least_important);
    RUN(commands_are_not_dropped);
    RUN(queue_follows_the_parent);
    RUN(quiet);
    RUN(pause_and_resume);
    RUN(pause_inside_a_quiet);
    RUN(stalled_frame_is_dropped);
    RUN(bad_frames_are_counted);
    RUN(hub_listens_to_tiles_only);
    RUN(own_hello_is_nobody);
    return T_DONE();
}
