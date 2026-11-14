/* The hub program, minus the hardware: DESIGN.md, "Hub".
 *
 * It keeps a table of the tiles it hears, lays them out on a grid, turns
 * their NOTE, STATE and POS frames into MIDI, runs the console, sends the
 * tiles commands, and holds the power policy.
 *
 * The port code, or the simulator, calls:
 *   hub_rx()            with bytes from the cable,
 *   hub_tick()          about once a millisecond,
 *   hub_tx()            when the cable's transmitter is free,
 *   hub_midi_get()      for the next MIDI message to give to USB,
 *   hub_console_rx()    with characters the computer typed,
 *   hub_console_read()  for characters to show it,
 *   hub_actions()       after hub_tick(), for the things only hardware can do,
 *   power_step(&hub.power, ...) with the board's measurements.
 *
 * hub.c holds the table, the frames and the command jobs; grid.c the
 * layout; console.c the text. */
#ifndef PIANO_HUB_H
#define PIANO_HUB_H

#include "ch224.h"
#include "node.h"
#include "notes.h"
#include "power.h"
#include "store.h"

#define HUB_MAX_TILES    32
#define HUB_MIDI_LEN     256    /* MIDI messages waiting for USB */
#define HUB_OUT_LEN      4096   /* console characters waiting for USB */
#define HUB_LINE_MAX     95     /* the longest command line */
#define HUB_PROBLEMS     8      /* cables that do not fit the grid, remembered */
#define HUB_KEY_BYTES    ((KEYS_N + 7) / 8)

#define HUB_QUIET_MS     20000u /* how long `bootloader <tile>` keeps the tiles quiet, unless told */

/* What hub_actions() can ask for. */
#define HUB_ACT_SAVE        0x01u   /* write hub_save_image() to flash, then hub_save_done() */
#define HUB_ACT_REBOOT      0x02u
#define HUB_ACT_BOOTLOADER  0x04u   /* restart into the ROM bootloader (USB DFU) */
#define HUB_ACT_BRIDGE      0x08u   /* make the serial port a plain UART: see hub_t.bridge */

typedef struct {
    bool used;
    uint32_t id;
    uint32_t last_heard;
    bool silent;                /* not heard for TILE_SILENT_MS: its keys were let go */

    /* what its last INFO said */
    bool have_info;
    uint16_t version;
    uint8_t flags, cols, rows;
    uint8_t parent, hops;
    uint32_t nbr_id[TILE_PORTS];
    uint8_t nbr_port[TILE_PORTS];
    uint16_t uptime, crc_drops, queue_drops;

    /* where it is */
    bool placed;
    int8_t u, v;

    /* its keys */
    uint8_t on[HUB_KEY_BYTES];  /* the keys the hub holds on */
    uint8_t diff[HUB_KEY_BYTES];/* the keys the last STATE disagreed about */
    uint8_t note[KEYS_N];       /* the MIDI note each held key started, 0xFF: none */
    bool have_events;
    uint8_t events;             /* the tile's event counter, as far as the hub has followed it */
    uint8_t behind;             /* STATEs in a row that carried an older counter */

    bool have_counter;
    uint8_t last_counter;       /* the counter of the last command sent to it */
} hub_tile_t;

/* A cable the layout could not use. */
enum { PROBLEM_PORTS, PROBLEM_PLACE };
typedef struct {
    uint8_t what;               /* PROBLEM_PORTS: an E port cabled to an N port, say;
                                 * PROBLEM_PLACE: it would put two tiles in one place */
    uint32_t a, b;              /* the two tiles, lower ID first */
    uint8_t a_port, b_port;
} hub_problem_t;

/* What a console command that talks to tiles is doing. */
enum { JOB_NONE, JOB_PLAIN, JOB_PING, JOB_GET, JOB_CAL_STOP };     /* what to print for a reply */
enum { STAGE_TILES, STAGE_FLASH };

typedef struct {
    uint8_t kind;               /* JOB_NONE: the console is free */
    uint8_t stage;
    uint8_t op;
    uint8_t arg[5];
    uint8_t arg_len;
    uint8_t param;              /* JOB_GET: which parameter */

    uint32_t target[HUB_MAX_TILES];
    uint8_t ntargets, at;       /* tiles to do, and how many are done */
    uint8_t failed;

    bool waiting;               /* a command is out, its reply is not in */
    bool replied;
    uint8_t counter, tries;
    uint32_t first_sent, sent_at;
    uint8_t reply[1 + 4];       /* status, data */
    uint8_t reply_len;
    uint32_t took;              /* ms from the first copy to the reply */

    bool then_flash;            /* after the tiles: save the hub's own settings */
    bool then_quiet;            /* after the tiles: QUIET to everybody for quiet_ms */
    bool flash_done, flash_ok;
    uint16_t quiet_ms;
} hub_job_t;

typedef struct {
    uint32_t baud;
    char parity;                /* 'n', 'e' or 'o' */
    bool swapped;               /* false: send on pin A, listen on pin B */
} hub_bridge_t;

typedef struct {
    uint32_t notes_old;         /* NOTE frames that came after a newer one */
    uint32_t notes_lost;        /* NOTE frames that never came (gaps in the counter) */
    uint32_t healed_on, healed_off;     /* keys set right by STATE */
    uint32_t silent;            /* times a tile went silent with keys down or not */
    uint32_t no_room;           /* frames from tiles the table had no room for */
    uint32_t midi_lost;         /* MIDI messages dropped: nobody took them */
    uint32_t out_lost;          /* console characters dropped likewise */
    uint32_t no_answer;         /* commands given up on */
} hub_stats_t;

typedef struct {
    node_t node;
    hub_settings_t set;
    uint32_t now;               /* the time of the call in progress */
    uint32_t started;

    hub_tile_t tile[HUB_MAX_TILES];
    uint8_t held[128];          /* how many keys hold each MIDI note */

    /* the grid */
    bool grid_dirty;            /* the layout has to be worked out again */
    uint8_t width, height;      /* in tiles; 0 when no tile is placed */
    hub_problem_t problem[HUB_PROBLEMS];
    uint8_t nproblems;

    /* MIDI out: a ring of three-byte messages */
    uint8_t midi[HUB_MIDI_LEN][3];
    uint16_t midi_head, midi_count;

    /* console: the line being typed, and a ring of characters to show */
    char line[HUB_LINE_MAX + 1];
    uint8_t line_len;
    bool line_over;             /* the line was too long */
    char out[HUB_OUT_LEN];
    uint16_t out_head, out_count;
    bool stream_events;         /* print every key event, and what tiles log */
    uint32_t stream_adc;        /* print this tile's RAW frames; 0: nobody's */

    /* commands to tiles */
    hub_job_t job;
    uint8_t counter;            /* the last command counter used */
    bool have_bcast;
    uint8_t last_bcast;         /* ... and the last one used for everybody */
    uint8_t bcast_left;         /* copies of the command for everybody still to send */
    uint32_t bcast_next;
    uint8_t bcast[CMD_HEAD_LEN + 4];
    uint8_t bcast_len;

    /* things only hardware can do */
    uint8_t actions;            /* HUB_ACT_ bits not yet collected */
    uint8_t later;              /* HUB_ACT_REBOOT or _BOOTLOADER, due at later_at */
    uint32_t later_at;
    hub_bridge_t bridge;
    bool bridging;

    power_t power;
    ch224_t pd;                 /* the port sets this up and polls it; the console shows it */
    hub_stats_t stats;
} hub_t;

/* uid: the chip's 96-bit ID. record: what flash holds (may be NULL or
 * rubbish: then defaults). */
void hub_init(hub_t *h, const uint8_t uid[12], const uint8_t *record, size_t record_len,
              uint32_t now);

void hub_rx(hub_t *h, const uint8_t *data, size_t len, uint32_t now);
void hub_tick(hub_t *h, uint32_t now);

static inline size_t hub_tx(hub_t *h, uint8_t *wire)
{
    return node_tx(&h->node, 0, wire);
}

static inline bool hub_swapped(const hub_t *h)
{
    return node_swapped(&h->node, 0);
}

/* The next MIDI message (status, two data bytes). False: none. */
bool hub_midi_get(hub_t *h, uint8_t msg[3]);

/* Characters from the computer. Returns how many were taken: fewer than
 * `len` when a command is still running, and then the rest should be
 * offered again later. While the bridge is on they are thrown away. */
size_t hub_console_rx(hub_t *h, const uint8_t *data, size_t len, uint32_t now);

/* Characters for the computer; returns how many were written to buf. */
size_t hub_console_read(hub_t *h, uint8_t *buf, size_t max);

/* The HUB_ACT_ bits that came up since the last call. */
unsigned hub_actions(hub_t *h);

/* For HUB_ACT_SAVE: the record to write, and how the writing went. */
void hub_save_image(const hub_t *h, uint8_t out[STORE_HUB_LEN]);
void hub_save_done(hub_t *h, bool ok);

/* The bridge (HUB_ACT_BRIDGE) is over: the serial port is the console again
 * and the cable speaks the protocol again, unless the quiet that a
 * `bootloader <tile>` or `quiet` began before the bridge still has time to
 * run: then the cable stays silent until that is over (see node_pause()). */
void hub_bridge_end(hub_t *h, uint32_t now);

/* ---- between hub.c, grid.c and console.c ---- */

#define KEY_BIT(map, k)  (((map)[(k) >> 3] >> ((k) & 7)) & 1u)

/* Work out where every tile is: sets placed, u, v, width, height, problems. */
void grid_layout(hub_t *h);

/* The tile with this ID, or NULL. */
hub_tile_t *hub_find(hub_t *h, uint32_t id);

/* The tile on the hub's own cable, or 0. */
uint32_t hub_root(const hub_t *h);

/* Add text to the console's output. hub_say ends the line too. */
void hub_print(hub_t *h, const char *f, ...) __attribute__((format(printf, 2, 3)));
void hub_say(hub_t *h, const char *f, ...) __attribute__((format(printf, 2, 3)));

/* Start a command job; the console is busy until it has printed its `ok`
 * or `error`. The targets are in h->job.target already. */
void hub_job_start(hub_t *h, uint8_t kind, uint8_t op, const uint8_t *arg, uint8_t arg_len);

/* Send a command to every tile, without replies. */
void hub_broadcast(hub_t *h, uint8_t op, const uint8_t *arg, uint8_t arg_len);

/* Stop every note that sounds. Keys that are down stay silent until they
 * are pressed again. */
void hub_release_all(hub_t *h);

/* That, and MIDI's "all notes off". */
void hub_panic(hub_t *h);

/* Run one console line. */
void console_line(hub_t *h, char *line);

/* What a job prints for one tile's reply. */
void console_reply(hub_t *h, const hub_tile_t *t, uint8_t status, const uint8_t *data, uint8_t n);

const char *console_param_name(uint8_t param);
const char *console_status_name(uint8_t status);

#endif
