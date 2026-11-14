#include "tile.h"

#include <string.h>

#define RAW_PER_FRAME  20       /* a scan goes out as two RAW frames */
#define ST_LATER       0xFFu    /* not a status on the wire: the reply comes when the flash is done */

/* ---------------------------------------------------------- what it sends --- */

static void on_note(void *ctx, uint8_t key, bool on, uint16_t speed)
{
    tile_t *t = ctx;
    uint8_t body[NOTE_LEN - UP_HEAD_LEN];

    /* The counter moves even if the frame cannot be sent: the hub sees the
     * gap and takes the next STATE at its word. */
    t->events++;
    body[0] = t->events;
    body[1] = (uint8_t)(key | (on ? 0x80u : 0u));
    put16(body + 2, speed);
    node_send_up(&t->node, FT_NOTE, body, sizeof body);
}

static void send_state(tile_t *t)
{
    uint8_t body[STATE_LEN - UP_HEAD_LEN];

    body[0] = t->events;
    keys_bitmap(&t->keys, body + 1);
    node_send_up(&t->node, FT_STATE, body, sizeof body);
}

static void send_info(tile_t *t)
{
    const node_t *n = &t->node;
    uint8_t body[INFO_LEN - UP_HEAD_LEN];
    uint8_t flags = 0;
    uint32_t up = since(t->now, t->started) / 1000u;

    if (keys_measured(&t->keys) == KEYS_N)
        flags |= INFO_CALIBRATED;
    if (t->keys.calibrating)
        flags |= INFO_CALIBRATING;
    if (t->raw)
        flags |= INFO_RAW;

    put16(body, FW_VERSION);
    body[2] = flags;
    body[3] = KEYS_COLS;
    body[4] = KEYS_ROWS;
    body[5] = n->parent;
    body[6] = n->parent == PORT_NONE ? 0xFF : n->parent_hops;
    for (uint8_t p = 0; p < TILE_PORTS; p++) {
        put32(body + 7 + 5 * p, n->port[p].link.nbr_id);
        body[7 + 5 * p + 4] = n->port[p].link.nbr_port;
    }
    put16(body + 27, (uint16_t)(up > 0xFFFFu ? 0xFFFFu : up));
    put16(body + 29, n->crc_drops);
    put16(body + 31, n->queue_drops);
    node_send_up(&t->node, FT_INFO, body, sizeof body);
}

/* The keys whose depth is not what the hub was last told. */
static void send_pos(tile_t *t)
{
    uint8_t body[2 * POS_MAX_PAIRS];
    uint8_t n = 0, i = t->pos_from;

    for (uint8_t seen = 0; seen < KEYS_N && n < POS_MAX_PAIRS; seen++) {
        uint8_t d = keys_depth(&t->keys, i);
        if (d != t->depth_sent[i]) {
            body[2 * n] = i;
            body[2 * n + 1] = d;
            n++;
        }
        i = (uint8_t)((i + 1) % KEYS_N);
    }
    if (n == 0 || !node_send_up(&t->node, FT_POS, body, (uint8_t)(2 * n)))
        return;                 /* not sent: the same keys are due next time */
    for (uint8_t j = 0; j < n; j++)
        t->depth_sent[body[2 * j]] = body[2 * j + 1];
    t->pos_from = i;
}

static void send_raw(tile_t *t)
{
    uint8_t body[3 + 2 * RAW_PER_FRAME];

    t->raw_scan++;
    for (uint8_t first = 0; first < KEYS_N; first += RAW_PER_FRAME) {
        body[0] = t->raw_scan;
        body[1] = first;
        body[2] = RAW_PER_FRAME;
        for (uint8_t j = 0; j < RAW_PER_FRAME; j++)
            put16(body + 3 + 2 * j, t->adc[first + j]);
        node_send_up(&t->node, FT_RAW, body, sizeof body);
    }
}

/* --------------------------------------------------------------- commands --- */

/* Do what a command says. Returns the status for the reply and, in
 * data/dn, what goes after it. */
static uint8_t run(tile_t *t, uint8_t op, const uint8_t *arg, uint8_t n, bool reply_wanted,
                   uint8_t counter, uint8_t data[4], uint8_t *dn)
{
    *dn = 0;
    switch (op) {
    case OP_PING:
        return ST_OK;

    case OP_GET:
        if (n < 1)
            return ST_BAD_ARG;
        if (arg[0] < 1 || arg[0] > TILE_PARAMS)
            return ST_UNKNOWN;
        put32(data, (uint32_t)t->keys.param[arg[0]]);
        *dn = 4;
        return ST_OK;

    case OP_SET:
        if (n < 5)
            return ST_BAD_ARG;
        if (arg[0] < 1 || arg[0] > TILE_PARAMS)
            return ST_UNKNOWN;
        return keys_set_param(&t->keys, arg[0], (int32_t)get32(arg + 1)) ? ST_OK : ST_BAD_ARG;

    case OP_SAVE:
        if (t->saving)
            return ST_BUSY;
        t->saving = true;
        t->save_reply = reply_wanted;
        t->save_counter = counter;
        t->actions |= TILE_ACT_SAVE;
        return ST_LATER;

    case OP_CAL:
        if (n < 1 || arg[0] > CAL_FORGET)
            return ST_BAD_ARG;
        data[0] = keys_cal(&t->keys, arg[0]);
        if (arg[0] == CAL_STOP)
            *dn = 1;            /* how many keys were measured */
        t->node.changed = true; /* the INFO flags changed: tell the hub now */
        return ST_OK;

    case OP_RAW:
        if (n < 1 || arg[0] > 1)
            return ST_BAD_ARG;
        t->raw = arg[0] != 0;
        t->next_raw = t->now;
        t->node.changed = true;
        return ST_OK;

    case OP_REBOOT:
    case OP_BOOTLOADER:
        t->later = op == OP_REBOOT ? TILE_ACT_REBOOT : TILE_ACT_BOOTLOADER;
        t->later_at = t->now + ACTION_DELAY_MS;
        return ST_OK;

    case OP_QUIET:
        if (n < 2)
            return ST_BAD_ARG;
        node_quiet(&t->node, get16(arg), t->now);
        return ST_OK;

    default:
        return ST_UNKNOWN;
    }
}

/* A hub that was gone long enough to be forgotten may be a different one,
 * or the same one started again: its counters mean nothing now. */
static void check_epoch(tile_t *t)
{
    if (t->epoch != t->node.epoch) {
        t->epoch = t->node.epoch;
        t->have_cmd = false;
        t->have_bcast = false;
        t->reply_len = 0;
    }
}

/* The node hands over a CMD aimed at this tile or at every tile. */
static void on_cmd(void *ctx, uint8_t port, uint8_t type, const uint8_t *pl, uint8_t len,
                   uint32_t now)
{
    tile_t *t = ctx;
    uint8_t data[4], dn;

    (void)port;
    if (type != FT_CMD || len < CMD_HEAD_LEN)
        return;
    t->now = now;
    check_epoch(t);

    bool all = get32(pl) == ID_ALL;
    uint8_t counter = pl[4], op = pl[5];
    const uint8_t *arg = pl + CMD_HEAD_LEN;
    uint8_t n = (uint8_t)(len - CMD_HEAD_LEN);

    if (all) {
        /* For everybody: nobody answers, so the hub sends it several times
         * with one counter. Run it the first time. */
        if (t->have_bcast && counter == t->last_bcast)
            return;
        t->have_bcast = true;
        t->last_bcast = counter;
        run(t, op, arg, n, false, counter, data, &dn);
        return;
    }

    if (t->have_cmd && counter == t->last_cmd) {
        /* A repeat: the reply was lost. Say it again; do nothing twice. */
        if (t->reply_len)
            node_send_up(&t->node, FT_REPLY, t->reply, t->reply_len);
        return;
    }
    t->have_cmd = true;
    t->last_cmd = counter;
    t->reply_len = 0;

    uint8_t status = run(t, op, arg, n, true, counter, data, &dn);
    if (status == ST_LATER)
        return;
    t->reply[0] = counter;
    t->reply[1] = status;
    memcpy(t->reply + 2, data, dn);
    t->reply_len = (uint8_t)(2 + dn);
    node_send_up(&t->node, FT_REPLY, t->reply, t->reply_len);
}

void tile_save_image(const tile_t *t, uint8_t out[STORE_TILE_LEN])
{
    tile_record_t rec;

    memcpy(rec.param, t->keys.param, sizeof rec.param);
    keys_get_cal(&t->keys, rec.cal);
    store_tile_pack(&rec, out);
}

void tile_save_done(tile_t *t, bool ok)
{
    uint8_t reply[2];

    if (!t->saving)
        return;
    t->saving = false;
    if (!t->save_reply)
        return;
    reply[0] = t->save_counter;
    reply[1] = ok ? ST_OK : ST_FAILED;
    node_send_up(&t->node, FT_REPLY, reply, sizeof reply);
    if (t->have_cmd && t->last_cmd == t->save_counter) {
        memcpy(t->reply, reply, sizeof reply);
        t->reply_len = sizeof reply;
    }
}

/* ------------------------------------------------------------- the program --- */

void tile_init(tile_t *t, const uint8_t uid[12], const uint8_t *record, size_t record_len,
               uint32_t scan_hz, uint32_t now)
{
    tile_record_t rec;

    memset(t, 0, sizeof *t);
    node_init(&t->node, KIND_TILE, TILE_PORTS, id_from_uid(uid), on_cmd, t, now);
    keys_init(&t->keys, scan_hz, on_note, t);
    if (store_tile_unpack(record, record_len, &rec)) {
        /* parameters that make no sense leave the defaults; the calibration
         * is checked key by key */
        keys_load_params(&t->keys, rec.param);
        keys_load_cal(&t->keys, rec.cal);
    }
    t->now = now;
    t->started = now;
    t->epoch = t->node.epoch;
    /* not every tile at the same millisecond */
    t->next_state = now + rng_range(&t->node.rng, 1, STATE_PERIOD_MS);
    t->next_info = now + rng_range(&t->node.rng, 1, INFO_PERIOD_MS);
    t->next_pos = now;
    t->next_raw = now;
}

void tile_rx(tile_t *t, uint8_t port, const uint8_t *data, size_t len, uint32_t now)
{
    t->now = now;
    node_rx(&t->node, port, data, len, now);
}

void tile_scan(tile_t *t, uint32_t scan_no, const uint16_t adc[KEYS_N], uint32_t now)
{
    t->now = now;
    memcpy(t->adc, adc, sizeof t->adc);
    keys_scan(&t->keys, scan_no, adc);
}

void tile_tick(tile_t *t, uint32_t now)
{
    t->now = now;
    node_tick(&t->node, now);
    check_epoch(t);

    if (t->later && reached(now, t->later_at)) {
        t->actions |= t->later;
        t->later = 0;
    }

    if (t->node.changed || reached(now, t->next_info)) {
        t->node.changed = false;
        t->next_info = now + INFO_PERIOD_MS;
        send_info(t);
    }
    if (reached(now, t->next_state)) {
        t->next_state = now + STATE_PERIOD_MS;
        send_state(t);
    }
    if (t->keys.param[PARAM_POS_MODE] != 0 && reached(now, t->next_pos)) {
        t->next_pos = now + (uint32_t)t->keys.param[PARAM_POS_INTERVAL];
        send_pos(t);
    }
    if (t->raw && reached(now, t->next_raw)) {
        t->next_raw = now + (uint32_t)t->keys.param[PARAM_RAW_INTERVAL];
        send_raw(t);
    }
}

unsigned tile_actions(tile_t *t)
{
    unsigned a = t->actions;

    t->actions = 0;
    return a;
}
