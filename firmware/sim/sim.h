/* A keyboard on a PC: real tile programs and a real hub program (core/),
 * with simulated cables, keys, flash and time in place of the hardware.
 *
 * Time moves in steps of 100 us. In one step a transmitter at 1 Mbaud sends
 * 10 bytes and one at 4 Mbaud 40, one frame straight after another; a tile
 * scans its keys every 5 steps; every node's program is ticked every 10.
 * Bytes sent at one rate into a port that listens at another arrive as
 * rubbish. Each node has its own
 * clock, started at a random value when the node starts, as real ones do:
 * nothing in the protocol may compare one node's time with another's.
 *
 * What the simulator does that the real port code has to do too:
 *   - a port's pins change over (link.swapped) and its rate changes
 *     (link.rate) only between two frames, never with one half sent;
 *   - while flash is written the program stands still for SIM_FLASH_MS, the
 *     frame being sent goes on, and what arrives waits in a buffer;
 *   - REBOOT starts the program again from what is in flash.
 *
 * Every node has its program flash too (flashsim.h), with a program in slot
 * A as it comes from the factory. A start goes through the bootloader's
 * choice (fw_boot_choose): the node runs the slot that says, on trial or
 * not, and with nothing to start it sits in its ROM. The program that runs
 * is always this PC's tile.c or hub.c; the image in the slot only says how
 * it behaves (SIM_ACT_): a hanging one stands still until the watchdog the
 * bootloader started for a trial restarts the node, a deaf one runs and
 * hears nothing, an old one speaks as the program from before the two cable
 * rates did. Erasing and writing the program flash takes time like the
 * settings do, and power that goes meanwhile leaves the last operation
 * half done.
 *
 * Where the tiles' power comes from is the test's choice (sim_t.hub_feeds).
 * As the simulator starts, every node has power of its own, and a hub that
 * restarts leaves its tiles running. On the real boards the power goes
 * through the hub, whose two switches are off while its chip starts: with
 * hub_feeds a hub that restarts, hangs or sits in its ROM takes every tile
 * down, and they start afresh when its program feeds them again. What
 * decides between the hub's two inputs (core/power.c) is not part of this:
 * tests/test_power.c.
 *
 * Tests reach into the structs: s.hub.h is the hub, s.tile[i].t a tile. */
#ifndef PIANO_SIM_H
#define PIANO_SIM_H

#include "flashsim.h"
#include "hub.h"
#include "tile.h"

#define SIM_MAX_TILES     16
#define SIM_MAX_CABLES    32
#define SIM_HUB           (-1)  /* the hub, where a tile's number could stand */
#define SIM_NOBODY        (-2)

#define SIM_STEPS_PER_MS  10    /* a step is 100 us */
#define SIM_STEP_BYTES    10    /* 1 Mbaud, 10 bits to the byte */
#define SIM_STEP_TICKS    40    /* a step in bytes at the fastest rate, 4 Mbaud */
#define SIM_SCAN_STEPS    5
#define SIM_SCAN_HZ       2000u
#define SIM_FLASH_MS      40    /* erasing and writing the record; erasing a sector of program */
#define SIM_WORD_STEPS    1     /* writing four bytes of program: 100 us */
#define SIM_WDT_MS        4000  /* the watchdog a program on trial runs under */
#define SIM_FACTORY_LEN   4096  /* the program a node comes with */
#define SIM_OLD_VERSION   0x0001u   /* ... if that is a SIM_ACT_OLD one: FW_VERSION then */
#define SIM_BOOT_MS       20    /* from reset to the program's first line */
#define SIM_FEED_MS       300   /* from the hub's program's first line to power for the
                                 * tiles: the computer has to set its USB up first */
#define SIM_RX_MAX        16384 /* bytes a port keeps while the program is busy: the real
                                 * port's ring (port/at32f405/uart.h) */

#define SIM_MIDI_MAX      8192
#define SIM_TEXT_MAX      (1 << 16)
#define SIM_TYPED_MAX     1024

/* The frame being sent on a port. */
typedef struct {
    uint8_t wire[FRAME_MAX_WIRE];
    uint8_t len, sent;          /* len 0: nothing */
    bool lost;                  /* the cable loses this one */
    bool whole;                 /* every byte so far reached the other end */
} sim_tx_t;

typedef struct {
    int cable;                  /* -1: nothing plugged in */
    uint8_t end;                /* which end of that cable: 0 or 1 */
    bool swapped;               /* the pins as they are now */
    uint8_t rate;               /* RATE_*: what its UART is set to now */
    uint8_t junk;               /* bytes that came in too fast to make one of */
    sim_tx_t tx;
    uint8_t rx[SIM_RX_MAX];     /* arrived, not yet given to the program */
    uint16_t rx_len;
} sim_port_t;

typedef struct sim sim_t;

/* What a tile and the hub have in common. */
typedef struct {
    sim_t *sim;
    bool powered;               /* false: no power, or not started yet */
    bool off;                   /* sim_power() switched it off */
    bool halted;                /* in the ROM bootloader: there, but not ours */
    uint8_t uid[12];
    uint32_t clock;             /* its clock is the simulator's ms plus this */
    uint8_t phase;              /* ... ticking this many steps early */
    uint64_t boot_at;           /* the step it starts at; 0: not waiting to */
    uint64_t busy_until;        /* writing flash until this step */
    bool saving;
    bool have_flash;
    bool flash_fails;           /* set by a test: writes fail from now on */
    unsigned saves, reboots, bootloaders, rx_overruns;

    /* its program flash, and what the bootloader made of it at the last start */
    flashsim_t chip;
    uint8_t slot;               /* the slot it runs from; FW_NO_SLOT: nothing to run, it is in its ROM */
    bool trial;
    uint8_t act;                /* SIM_ACT_: how the program in that slot behaves */
    bool flashing;              /* what it is busy with is the program flash */
    uint64_t wdt_at;            /* the step the watchdog restarts it at; 0: it does not */
    unsigned wdt_resets, no_program;
} sim_node_t;

typedef struct {
    sim_node_t n;
    tile_t t;
    sim_port_t port[TILE_PORTS];
    uint8_t flash[STORE_TILE_LEN], image[STORE_TILE_LEN];

    /* the keys: travel 0 (up) to 65535 (down), and where each is going */
    int32_t x[KEYS_N], from[KEYS_N], to[KEYS_N];
    uint64_t t0[KEYS_N], t1[KEYS_N];
    bool moving[KEYS_N];
    uint16_t rest[KEYS_N];      /* what each sensor reads with the key up */
    int16_t swing[KEYS_N];      /* ... and how much more on the floor */
    uint32_t scan;
    uint8_t scan_phase;
} sim_tile_t;

typedef struct {
    sim_node_t n;
    hub_t h;
    sim_port_t port[1];
    uint8_t flash[STORE_HUB_LEN], image[STORE_HUB_LEN];
    unsigned bridges;
} sim_hub_t;

typedef struct {
    bool used, plugged;
    bool crossed;               /* pin A of one end goes to pin B of the other */
    int node[2];
    uint8_t port[2];
    /* per direction; [0] is what end 0 sends */
    uint16_t loss[2];           /* frames in a thousand that vanish */
    uint16_t corrupt[2];        /* frames in a thousand with one bit turned over */
    uint8_t drop_type[2];       /* the next drop_left frames of this type vanish; 0: any type */
    uint16_t drop_left[2];
    bool fast_dead[2];          /* what is sent above the base rate arrives as rubbish */
    uint32_t sent[2], arrived[2], lost[2];      /* frames */
} sim_cable_t;

/* Called for every good frame that reached the other end of a cable. */
typedef void (*sim_tap_fn)(void *ctx, int from, uint8_t from_port, int to, uint8_t to_port,
                           uint8_t type, const uint8_t *payload, uint8_t len);

struct sim {
    uint64_t t;                 /* steps since the start */
    rng_t rng;
    sim_hub_t hub;
    sim_tile_t tile[SIM_MAX_TILES];
    int ntiles;
    sim_cable_t cable[SIM_MAX_CABLES];
    int ncables;

    /* the sensors of tiles added from now on, and their noise */
    int rest_spread;            /* ADC counts either way */
    int swing_spread;           /* percent either way */
    int noise;                  /* ADC counts either way, every reading */

    /* Not 0: a node that starts from now on has a clock that wraps round to
     * 0 this many ms later. (Otherwise clocks start anywhere.) */
    uint32_t wrap_ms;

    /* True: the tiles have their power from the hub, as on the real boards.
     * Its program switches the power on SIM_FEED_MS after it starts, and
     * when the program stops, for whatever reason, every tile stops with
     * it. For a test to set, at any time. */
    bool hub_feeds;
    uint64_t feed_at;           /* the step the hub's program switches the power on at */

    /* the computer: what the hub gave it, what it types */
    uint8_t midi[SIM_MIDI_MAX][3];
    uint64_t midi_t[SIM_MIDI_MAX];      /* the step each message came out at */
    int nmidi;
    char text[SIM_TEXT_MAX];
    size_t ntext;
    char typed[SIM_TYPED_MAX];
    size_t ntyped;

    sim_tap_fn tap;
    void *tap_ctx;
};

/* A hub, switched on, and nothing else. */
void sim_init(sim_t *s, uint32_t seed);

/* A tile, switched on. Returns its number. */
int sim_add_tile(sim_t *s);

/* A cable from node a's port to node b's (SIM_HUB's port is 0). Returns the
 * cable's number, or -1 if a port is taken or the table is full. */
int sim_cable(sim_t *s, int a, uint8_t a_port, int b, uint8_t b_port, bool crossed);
void sim_unplug(sim_t *s, int cable);
void sim_plug(sim_t *s, int cable);

/* Lose the next `count` frames of `type` (0: any) that end `end` of the
 * cable sends and the other end would have heard. */
void sim_drop(sim_t *s, int cable, int end, uint8_t type, unsigned count);

/* Off: the node stops dead. On: it starts from what is in its flash; a
 * tile that has its power from the hub, once the hub feeds it. */
void sim_power(sim_t *s, int node, bool on);

/* The node came from the factory with a program that behaves like `act`
 * (SIM_ACT_): its flash is as it was then, with that program in slot A, and
 * the node starts again. */
void sim_factory(sim_t *s, int node, uint8_t act);

void sim_run(sim_t *s, uint32_t ms);

/* One step of 100 us: for a test that has to stop in the middle of something. */
void sim_step(sim_t *s);

/* Move a key to travel `to` (0..65535) in a straight line over `ms`. */
void sim_move(sim_t *s, int tile, int key, int to, uint32_t ms);

/* Characters for the hub's console. */
void sim_type(sim_t *s, const char *text);

/* The node's own clock, as its program sees it. */
uint32_t sim_now(const sim_t *s, int node);

/* Who is at the other end of the tile's parent port: SIM_HUB, a tile, or
 * SIM_NOBODY. */
int sim_toward_hub(const sim_t *s, int tile);

#endif
