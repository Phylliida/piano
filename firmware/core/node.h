/* A node: something with ports that speaks the protocol. A tile is a node
 * with four ports, the hub a node with one.
 *
 * The node owns each port's link, receive buffer and send queue, and does
 * what DESIGN.md's "Network" section says: a tile follows the hub's beacons
 * to pick a parent port, passes upstream frames toward it and commands away
 * from it; the hub sends the beacons. Whatever is addressed to this node is
 * handed to the program above (tile.c or hub.c) through `deliver`.
 *
 * The node never touches hardware. The port code, or the simulator, calls:
 *   node_rx()    with the bytes a port received,
 *   node_tick()  about once a millisecond,
 *   node_tx()    whenever a port's transmitter is free, to get the next frame,
 *   node_swapped() to learn which way round a port's pins should be. */
#ifndef PIANO_NODE_H
#define PIANO_NODE_H

#include "frame.h"
#include "link.h"

#define NODE_MAX_PORTS  4
#define NODE_QUEUE_LEN  24      /* frames waiting on one port */

typedef struct {
    uint8_t type;
    uint8_t len;
    uint8_t data[FRAME_MAX_PAYLOAD];
} node_frame_t;

/* A port's send queue. `order` lists the used slots, oldest first. */
typedef struct {
    node_frame_t slot[NODE_QUEUE_LEN];
    uint8_t order[NODE_QUEUE_LEN];
    uint8_t count;
    uint32_t free_slots;        /* bit i set: slot i is free */
} node_queue_t;

typedef struct {
    link_t link;
    frame_rx_t rx;
    uint32_t rx_last;           /* when bytes last arrived */
    node_queue_t queue;
    bool beacon_due;            /* `beacon` waits to go out; a newer one replaces it */
    uint8_t beacon[BEACON_LEN];
    uint8_t hops;               /* hops of the last beacon heard here, 0xFF: none */
} node_port_t;

/* A frame for this node: a CMD aimed at this tile, or (hub) any upstream
 * frame. payload is the whole payload, header included. */
typedef void (*node_deliver_fn)(void *ctx, uint8_t port, uint8_t type,
                                const uint8_t *payload, uint8_t len, uint32_t now);

enum { QUIET_OFF, QUIET_SOON, QUIET_ON };

typedef struct {
    uint8_t kind;               /* KIND_TILE or KIND_HUB */
    uint8_t nports;
    uint32_t id;
    rng_t rng;
    node_port_t port[NODE_MAX_PORTS];

    /* tile: the tree */
    uint8_t parent;             /* port toward the hub, or PORT_NONE */
    uint8_t parent_hops;        /* hops in the parent's beacons */
    bool have_seq;              /* last_seq means something */
    uint16_t last_seq;          /* the newest beacon forwarded */
    uint32_t hub_id;            /* the hub being followed, 0: none */
    uint32_t parent_heard;      /* when the parent's last new beacon came */
    uint32_t orphan_since;      /* when the parent was lost */
    uint8_t epoch;              /* +1 each time the hub is forgotten */

    /* hub: the beacons */
    uint16_t beacon_seq;
    uint32_t next_beacon;

    /* sending nothing for a while (QUIET, or the hub's bridge) */
    uint8_t quiet;
    bool quiet_forever;         /* until node_resume() */
    bool quiet_under;           /* a timed quiet was running when the pause began */
    uint32_t quiet_from, quiet_until;

    /* what INFO reports */
    uint16_t crc_drops;         /* frames that failed COBS, length or CRC */
    uint16_t queue_drops;       /* frames dropped from a full queue */
    bool changed;               /* a neighbour or the parent changed; the program clears it */

    node_deliver_fn deliver;
    void *ctx;
} node_t;

void node_init(node_t *n, uint8_t kind, uint8_t nports, uint32_t id,
               node_deliver_fn deliver, void *ctx, uint32_t now);

void node_rx(node_t *n, uint8_t port, const uint8_t *data, size_t len, uint32_t now);
void node_tick(node_t *n, uint32_t now);

/* The next frame to send on a port, as wire bytes (FRAME_MAX_WIRE at most).
 * Returns 0 when there is nothing to send. Call it only when the previous
 * frame has left: the node does not keep what it hands out. */
size_t node_tx(node_t *n, uint8_t port, uint8_t *wire);

static inline bool node_swapped(const node_t *n, uint8_t port)
{
    return n->port[port].link.swapped;
}

/* Tile: queue one of its own upstream frames. `body` is what follows the
 * ID. False if it was not queued: no parent, or the queue is full of more
 * important frames. */
bool node_send_up(node_t *n, uint8_t type, const uint8_t *body, uint8_t len);

/* Hub: queue a frame on its port. */
bool node_send_down(node_t *n, uint8_t type, const uint8_t *payload, uint8_t len);

/* Send nothing for `ms` milliseconds, starting QUIET_LEAD_MS from now so
 * that what is queued can still leave. */
void node_quiet(node_t *n, uint32_t ms, uint32_t now);

/* Send nothing from now until node_resume(). A pause that began inside a
 * node_quiet() does not cut it short: if that quiet still has time to run
 * at node_resume(), the node stays silent until it ends (everybody else on
 * the cables was told to keep quiet for as long, and whoever asked had a
 * reason). */
void node_pause(node_t *n, uint32_t now);
void node_resume(node_t *n, uint32_t now);

/* Exposed for the tests: how many frames wait on a port. */
static inline unsigned node_queued(const node_t *n, uint8_t port)
{
    return n->port[port].queue.count;
}

#endif
