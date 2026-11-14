#include "node.h"

#include <string.h>

/* ------------------------------------------------------------ send queues --- */

/* Which frames give way when a queue is full: the lowest number first. */
static uint8_t importance(uint8_t type)
{
    switch (type) {
    case FT_RAW:
    case FT_LOG:
        return 0;
    case FT_POS:
        return 1;
    case FT_INFO:
    case FT_STATE:
        return 2;
    case FT_NOTE:
    case FT_REPLY:
        return 3;
    default:
        /* an upstream type this firmware does not know: treat it like INFO.
         * Everything else here is a CMD, which gives way to nothing. */
        return FT_IS_UPSTREAM(type) ? 2 : 4;
    }
}

static void queue_clear(node_queue_t *q)
{
    q->count = 0;
    q->free_slots = (1u << NODE_QUEUE_LEN) - 1u;
}

static void queue_remove(node_queue_t *q, uint8_t pos)
{
    q->free_slots |= 1u << q->order[pos];
    memmove(&q->order[pos], &q->order[pos + 1], (size_t)(q->count - pos - 1));
    q->count--;
}

static void count_queue_drop(node_t *n)
{
    if (n->queue_drops < 0xFFFFu)
        n->queue_drops++;
}

/* Add a frame at the end. If the queue is full, the oldest of the least
 * important frames in it goes, unless the new frame is less important
 * still: then the new one is the one dropped. */
static bool queue_push(node_t *n, node_queue_t *q, uint8_t type, const uint8_t *data, uint8_t len)
{
    if (len > FRAME_MAX_PAYLOAD)
        return false;

    if (q->count == NODE_QUEUE_LEN) {
        uint8_t worst = 0xFF, worst_pos = 0;
        for (uint8_t pos = 0; pos < q->count; pos++) {
            uint8_t imp = importance(q->slot[q->order[pos]].type);
            if (imp < worst) {
                worst = imp;
                worst_pos = pos;
            }
        }
        count_queue_drop(n);
        if (importance(type) < worst)
            return false;
        queue_remove(q, worst_pos);
    }

    uint8_t s = 0;
    while (!((q->free_slots >> s) & 1u))
        s++;
    q->free_slots &= ~(1u << s);
    q->slot[s].type = type;
    q->slot[s].len = len;
    if (len)
        memcpy(q->slot[s].data, data, len);
    q->order[q->count++] = s;
    return true;
}

/* Throw away the upstream frames waiting in a queue. */
static void queue_drop_upstream(node_queue_t *q)
{
    uint8_t pos = 0;
    while (pos < q->count) {
        if (FT_IS_UPSTREAM(q->slot[q->order[pos]].type))
            queue_remove(q, pos);
        else
            pos++;
    }
}

/* Move the upstream frames of one queue to the end of another, in order. */
static void queue_move_upstream(node_t *n, node_queue_t *from, node_queue_t *to)
{
    uint8_t pos = 0;
    while (pos < from->count) {
        const node_frame_t *f = &from->slot[from->order[pos]];
        if (FT_IS_UPSTREAM(f->type)) {
            queue_push(n, to, f->type, f->data, f->len);
            queue_remove(from, pos);
        } else {
            pos++;
        }
    }
}

/* --------------------------------------------------------- tile: the tree --- */

static void lose_parent(node_t *n, uint32_t now)
{
    if (n->parent == PORT_NONE)
        return;
    /* nobody upstream would take these now */
    queue_drop_upstream(&n->port[n->parent].queue);
    n->parent = PORT_NONE;
    n->orphan_since = now;
    n->changed = true;
}

static void set_parent(node_t *n, uint8_t port)
{
    if (n->parent == port)
        return;
    if (n->parent != PORT_NONE)
        queue_move_upstream(n, &n->port[n->parent].queue, &n->port[port].queue);
    n->parent = port;
    n->changed = true;
}

static void tile_beacon(node_t *n, uint8_t port, const uint8_t *pl, uint32_t now)
{
    uint32_t hub = get32(pl);
    uint16_t seq = get16(pl + 4);
    uint8_t hops = pl[6];

    n->port[port].hops = hops;

    /* One hub at a time: while there is a parent, another hub's beacons are
     * ignored. Without a parent, a different hub starts from scratch, its
     * sequence numbers being its own. */
    if (hub != n->hub_id) {
        if (n->parent != PORT_NONE)
            return;
        n->have_seq = false;
    }

    if (n->have_seq && (int16_t)(seq - n->last_seq) <= 0)
        return;                             /* already forwarded this one, or a newer one */
    if (port != n->parent) {
        if (n->parent != PORT_NONE && hops >= n->parent_hops)
            return;                         /* no shorter than the way we have */
        set_parent(n, port);
    }

    n->hub_id = hub;
    n->parent_hops = hops;
    n->last_seq = seq;
    n->have_seq = true;
    n->parent_heard = now;

    if (hops == 0xFF)
        return;
    for (uint8_t q = 0; q < n->nports; q++) {
        if (q == port)
            continue;
        node_port_t *p = &n->port[q];
        memcpy(p->beacon, pl, BEACON_LEN);
        p->beacon[6] = (uint8_t)(hops + 1u);
        p->beacon_due = true;
    }
}

/* ---------------------------------------------------------------- receive --- */

static void node_frame(node_t *n, uint8_t port, uint8_t type,
                       const uint8_t *pl, uint8_t len, uint32_t now)
{
    node_port_t *p = &n->port[port];

    link_heard(&p->link, now);

    if (type == FT_HELLO) {
        if (link_hello(&p->link, pl, len, n->id) & LINK_NBR)
            n->changed = true;
        return;
    }

    if (n->kind == KIND_HUB) {
        /* The hub listens to tiles and to nothing else. */
        if (FT_IS_UPSTREAM(type) && len >= UP_HEAD_LEN && n->deliver)
            n->deliver(n->ctx, port, type, pl, len, now);
        return;
    }

    if (type == FT_BEACON) {
        if (len >= BEACON_LEN)
            tile_beacon(n, port, pl, now);
    } else if (type == FT_CMD) {
        /* Only from the parent, and on to every other port that is up: so
         * a command reaches each tile once, down the tree. */
        if (len < CMD_HEAD_LEN || port != n->parent)
            return;
        for (uint8_t q = 0; q < n->nports; q++) {
            if (q != port && n->port[q].link.up)
                queue_push(n, &n->port[q].queue, type, pl, len);
        }
        uint32_t target = get32(pl);
        if ((target == n->id || target == ID_ALL) && n->deliver)
            n->deliver(n->ctx, port, type, pl, len, now);
    } else if (FT_IS_UPSTREAM(type)) {
        /* Toward the hub, unchanged. From the parent's side it would be
         * going the wrong way; and our own frames never come back. */
        if (len < UP_HEAD_LEN || n->parent == PORT_NONE || port == n->parent)
            return;
        if (get32(pl) == n->id)
            return;
        queue_push(n, &n->port[n->parent].queue, type, pl, len);
    }
}

void node_rx(node_t *n, uint8_t port, const uint8_t *data, size_t len, uint32_t now)
{
    node_port_t *p = &n->port[port];
    uint8_t type, plen;
    uint8_t payload[FRAME_MAX_PAYLOAD];

    if (n->quiet == QUIET_ON)
        return;
    p->rx_last = now;
    for (size_t i = 0; i < len; i++) {
        switch (frame_rx_byte(&p->rx, data[i], &type, payload, &plen)) {
        case FRAME_OK:
            node_frame(n, port, type, payload, plen, now);
            break;
        case FRAME_BAD:
            if (n->crc_drops < 0xFFFFu)
                n->crc_drops++;
            break;
        case FRAME_NONE:
            break;
        }
    }
}

/* ------------------------------------------------------------------ quiet --- */

static void quiet_begin(node_t *n, uint32_t now)
{
    for (uint8_t i = 0; i < n->nports; i++) {
        node_port_t *p = &n->port[i];
        queue_clear(&p->queue);
        p->beacon_due = false;
        p->link.hello_due = false;
        frame_rx_reset(&p->rx);
    }
    lose_parent(n, now);
    n->quiet = QUIET_ON;
}

void node_quiet(node_t *n, uint32_t ms, uint32_t now)
{
    if (n->quiet == QUIET_ON)
        return;
    n->quiet = QUIET_SOON;
    n->quiet_forever = false;
    n->quiet_from = now + QUIET_LEAD_MS;
    n->quiet_until = n->quiet_from + ms;
}

void node_pause(node_t *n, uint32_t now)
{
    /* a quiet that is running, or about to, goes on underneath */
    if (!n->quiet_forever)
        n->quiet_under = n->quiet != QUIET_OFF;
    quiet_begin(n, now);
    n->quiet_forever = true;
}

void node_resume(node_t *n, uint32_t now)
{
    if (n->quiet_forever && n->quiet_under && !reached(now, n->quiet_until)) {
        /* back to the quiet underneath: node_tick() ends it at its time */
        n->quiet_forever = false;
        n->quiet_under = false;
        return;
    }
    n->quiet = QUIET_OFF;
    n->quiet_forever = false;
    n->quiet_under = false;
    /* Everybody around was quiet too, so what the links knew is stale. The
     * pins stay the way they were: that way round worked. */
    for (uint8_t i = 0; i < n->nports; i++) {
        link_restart(&n->port[i].link, &n->rng, now);
        n->port[i].hops = 0xFF;
    }
    n->orphan_since = now;
    n->next_beacon = now + BEACON_PERIOD_MS;
    n->changed = true;
}

/* ------------------------------------------------------------------- tick --- */

void node_tick(node_t *n, uint32_t now)
{
    if (n->quiet == QUIET_SOON && reached(now, n->quiet_from))
        quiet_begin(n, now);
    if (n->quiet == QUIET_ON) {
        if (n->quiet_forever) {
            /* forgotten once it is over, so that a pause of weeks cannot
             * meet the same clock value again */
            if (n->quiet_under && reached(now, n->quiet_until))
                n->quiet_under = false;
            return;
        }
        if (!reached(now, n->quiet_until))
            return;
        node_resume(n, now);
    }

    for (uint8_t i = 0; i < n->nports; i++) {
        node_port_t *p = &n->port[i];

        /* Frames are sent in one piece. Bytes that stopped half way through
         * one are the tail of something cut off, not the start of a frame. */
        if ((p->rx.len || p->rx.too_long) && since(now, p->rx_last) >= RX_GAP_MS)
            frame_rx_reset(&p->rx);

        unsigned ev = link_tick(&p->link, &n->rng, now);
        if (ev & LINK_SWAPPED)
            frame_rx_reset(&p->rx);
        if (ev & LINK_NBR)
            n->changed = true;
        if (ev & LINK_WENT_DOWN) {
            p->hops = 0xFF;
            if (i == n->parent)
                lose_parent(n, now);
        }
    }

    if (n->kind == KIND_TILE) {
        if (n->parent != PORT_NONE && since(now, n->parent_heard) >= PARENT_TIMEOUT_MS)
            lose_parent(n, now);
        /* Long enough alone: forget the hub, so that one that restarted and
         * counts from zero again is believed. */
        if (n->parent == PORT_NONE && (n->have_seq || n->hub_id != ID_NONE) &&
            since(now, n->orphan_since) >= SEQ_FORGET_MS) {
            n->have_seq = false;
            n->hub_id = ID_NONE;
            n->epoch++;
        }
    } else if (reached(now, n->next_beacon)) {
        node_port_t *p = &n->port[0];
        n->next_beacon = now + BEACON_PERIOD_MS;
        put32(p->beacon, n->id);
        put16(p->beacon + 4, n->beacon_seq++);
        p->beacon[6] = 0;       /* hops */
        p->beacon[7] = 0;       /* flags */
        p->beacon_due = true;
    }
}

/* ------------------------------------------------------------------- send --- */

size_t node_tx(node_t *n, uint8_t port, uint8_t *wire)
{
    node_port_t *p = &n->port[port];
    uint8_t payload[HELLO_LEN];

    if (n->quiet == QUIET_ON)
        return 0;

    /* HELLO keeps the link up and a beacon is only worth its newest copy,
     * so both go before anything queued. */
    if (p->link.hello_due) {
        p->link.hello_due = false;
        link_hello_payload(payload, n->kind, n->id, port);
        return frame_encode(FT_HELLO, payload, HELLO_LEN, wire);
    }
    if (p->beacon_due) {
        p->beacon_due = false;
        return frame_encode(FT_BEACON, p->beacon, BEACON_LEN, wire);
    }
    if (p->queue.count) {
        const node_frame_t *f = &p->queue.slot[p->queue.order[0]];
        size_t w = frame_encode(f->type, f->data, f->len, wire);
        queue_remove(&p->queue, 0);
        return w;
    }
    return 0;
}

bool node_send_up(node_t *n, uint8_t type, const uint8_t *body, uint8_t len)
{
    uint8_t payload[FRAME_MAX_PAYLOAD];

    if (n->parent == PORT_NONE || n->quiet == QUIET_ON)
        return false;
    if ((size_t)len + UP_HEAD_LEN > FRAME_MAX_PAYLOAD)
        return false;
    put32(payload, n->id);
    if (len)
        memcpy(payload + UP_HEAD_LEN, body, len);
    return queue_push(n, &n->port[n->parent].queue, type, payload, (uint8_t)(len + UP_HEAD_LEN));
}

bool node_send_down(node_t *n, uint8_t type, const uint8_t *payload, uint8_t len)
{
    if (n->quiet == QUIET_ON)
        return false;
    return queue_push(n, &n->port[0].queue, type, payload, len);
}

/* ------------------------------------------------------------------- init --- */

void node_init(node_t *n, uint8_t kind, uint8_t nports, uint32_t id,
               node_deliver_fn deliver, void *ctx, uint32_t now)
{
    memset(n, 0, sizeof *n);
    n->kind = kind;
    n->nports = nports;
    n->id = id;
    n->deliver = deliver;
    n->ctx = ctx;
    /* The ID seeds the dwell times, so two tiles do not swap in step. */
    rng_seed(&n->rng, id);

    n->parent = PORT_NONE;
    n->orphan_since = now;
    n->next_beacon = now + HUB_START_MS;
    for (uint8_t i = 0; i < nports; i++) {
        node_port_t *p = &n->port[i];
        link_init(&p->link, &n->rng, now);
        frame_rx_reset(&p->rx);
        queue_clear(&p->queue);
        p->hops = 0xFF;
    }
}
