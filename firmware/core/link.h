/* One port's link: who is on the other end of the cable, whether anybody
 * is, which way round the two data pins are used, and how fast.
 *
 * A port starts straight (send on A, listen on B). A port that hears nothing
 * for its dwell time swaps and tries the other way; see DESIGN.md, "Which
 * way round the cable is". The port code reads `swapped` and sets the UART's
 * pin-swap bit to match.
 *
 * A port starts at the base rate, and two ports that both can go faster
 * agree on it in their HELLOs; see DESIGN.md, "How fast". The port code
 * reads `rate` and sets the UART to match, at the moment its transmitter is
 * idle and before it asks for the next frame. */
#ifndef PIANO_LINK_H
#define PIANO_LINK_H

#include "proto.h"
#include "util.h"

/* How far a port is with its cable's rate. */
enum {
    RS_BASE,        /* at the base rate */
    RS_ASKED,       /* at the base rate, having asked for more: waiting for the GO */
                    /* (from here to RS_ANSWER, and in RS_BACK, nothing but HELLOs goes out) */
    RS_CALL,        /* at the new rate behind the other end's GO: calling it until it answers */
    RS_WAIT,        /* at the new rate behind our own GO: silent until the other end calls */
    RS_ANSWER,      /* ... it called: calling back until it says it heard that */
    RS_FAST,        /* at the new rate, and each end knows the other hears it */
    RS_BACK,        /* at the base rate after a try that failed, or an asking nobody
                     * answered: saying so until the other end says it is there too */
};

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

    /* the rate */
    uint8_t rate;           /* RATE_*: what the UART is to be set to */
    uint8_t rate_max;       /* the fastest this port may go */
    uint8_t rate_state;     /* RS_* */
    uint8_t nbr_can;        /* the fastest the neighbour's last HELLO offered */
    uint8_t go_rate;        /* go_due: where to */
    uint8_t rate_fails;     /* tries in a row that failed with rate_nbr */
    bool lead;              /* our ID is the lower one: it is for us to ask */
    bool ask_due;           /* the next HELLO asks for a faster rate */
    bool go_due;            /* the next HELLO is a GO, and the rate changes behind it */
    bool retry_wait;        /* a try failed less than RATE_RETRY_MS ago */
    bool proven;            /* RS_FAST for RATE_PROVEN_MS and more */
    uint32_t rate_nbr;      /* whom rate_fails is about */
    uint32_t rate_since;    /* when rate_state was entered (RS_ANSWER: RS_WAIT) */
    uint32_t ticked;        /* the last link_tick() */
    bool stalled;           /* ... came RATE_FRESH_MS or more after the one before */
} link_t;

/* What link_tick() and link_hello() report. */
#define LINK_SWAPPED   0x01u    /* the pins were swapped just now */
#define LINK_WENT_DOWN 0x02u    /* the port was up and no longer is */
#define LINK_NBR       0x04u    /* the neighbour's identity changed */

void link_init(link_t *l, rng_t *rng, uint32_t now);

/* Start listening afresh, as after a pause in which nobody sent: at the
 * base rate, the pins as they are. */
void link_restart(link_t *l, rng_t *rng, uint32_t now);

/* Any good frame arrived. */
void link_heard(link_t *l, uint32_t now);

/* A HELLO payload arrived (after link_heard). own_id is this node's ID: a
 * HELLO from ourselves, over a cable from one of our ports to another, is no
 * neighbour. Returns LINK_NBR if the neighbour changed. */
unsigned link_hello(link_t *l, const uint8_t *payload, uint8_t len, uint32_t own_id, uint32_t now);

/* Run the timers. Returns LINK_* flags. */
unsigned link_tick(link_t *l, rng_t *rng, uint32_t now);

/* Write the HELLO that is due on this port (HELLO_LEN bytes). To be called
 * when that HELLO is the very next frame to leave: what it says about the
 * rate takes effect here. */
void link_hello_out(link_t *l, uint8_t *payload, uint8_t kind, uint32_t id, uint8_t port,
                    uint32_t now);

/* A HELLO payload (HELLO_LEN bytes) with nothing to say about rates: at the
 * base rate, offering no more. */
void link_hello_payload(uint8_t *payload, uint8_t kind, uint32_t id, uint8_t port);

/* SLOW_*: why this port is not at its fastest rate. */
uint8_t link_slow(const link_t *l);

/* Nothing but HELLOs may leave this port now: a change of rate is under way,
 * and whatever else left would be lost in it. */
static inline bool link_holds(const link_t *l)
{
    return l->rate_state != RS_BASE && l->rate_state != RS_FAST;
}

#endif
