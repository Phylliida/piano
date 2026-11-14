/* One port's link: who is on the other end of the cable, whether anybody
 * is, and which way round the two data pins are used.
 *
 * A port starts straight (send on A, listen on B). A port that hears nothing
 * for its dwell time swaps and tries the other way; see DESIGN.md, "Which
 * way round the cable is". The port code reads `swapped` and sets the UART's
 * pin-swap bit to match. */
#ifndef PIANO_LINK_H
#define PIANO_LINK_H

#include "proto.h"
#include "util.h"

typedef struct {
    bool swapped;           /* false: send on A, listen on B */
    bool up;                /* a good frame came in during the last LINK_UP_MS */
    bool hello_due;         /* the next thing to send on this port is a HELLO */
    uint8_t nbr_kind;       /* KIND_TILE or KIND_HUB; valid while nbr_id != 0 */
    uint8_t nbr_port;       /* which of the neighbour's ports this cable is on */
    uint16_t dwell;         /* how long to listen before swapping */
    uint32_t nbr_id;        /* 0: nobody known */
    uint32_t last_valid;    /* when the last good frame came in */
    uint32_t listen_from;   /* when this dwell began: the last good frame or swap */
    uint32_t next_hello;
} link_t;

/* What link_tick() and link_hello() report. */
#define LINK_SWAPPED   0x01u    /* the pins were swapped just now */
#define LINK_WENT_DOWN 0x02u    /* the port was up and no longer is */
#define LINK_NBR       0x04u    /* the neighbour's identity changed */

void link_init(link_t *l, rng_t *rng, uint32_t now);

/* Start listening afresh, as after a pause in which nobody sent. */
void link_restart(link_t *l, rng_t *rng, uint32_t now);

/* Any good frame arrived. */
void link_heard(link_t *l, uint32_t now);

/* A HELLO payload arrived (after link_heard). own_id is this node's ID: a
 * HELLO from ourselves, over a cable from one of our ports to another, is no
 * neighbour. Returns LINK_NBR if the neighbour changed. */
unsigned link_hello(link_t *l, const uint8_t *payload, uint8_t len, uint32_t own_id);

/* Run the timers. Returns LINK_* flags. */
unsigned link_tick(link_t *l, rng_t *rng, uint32_t now);

/* Write this port's HELLO payload (HELLO_LEN bytes). */
void link_hello_payload(uint8_t *payload, uint8_t kind, uint32_t id, uint8_t port);

#endif
