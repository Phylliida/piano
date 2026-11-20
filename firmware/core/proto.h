/* The numbers both programs must agree on: frame types, sizes, times.
 * DESIGN.md explains them; this file is that text as constants. */
#ifndef PIANO_PROTO_H
#define PIANO_PROTO_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define PROTO_VERSION      1
#define FW_VERSION         0x0002u      /* reported in INFO. 2: cables at two rates */

/* ---- frames ---- */
#define FRAME_MAX_PAYLOAD  60
#define FRAME_MAX_BODY     (1 + FRAME_MAX_PAYLOAD + 2)   /* type, payload, crc */
#define FRAME_MAX_WIRE     (FRAME_MAX_BODY + 2)          /* COBS adds 1; then the 0x00 */

enum {
    FT_HELLO  = 0x01,   /* one cable only, never forwarded */
    FT_BEACON = 0x10,   /* hub to everyone */
    FT_CMD    = 0x11,   /* hub to tiles */
    FT_NOTE   = 0x20,   /* 0x20..0x2F: tile to hub */
    FT_POS    = 0x21,
    FT_STATE  = 0x22,
    FT_INFO   = 0x23,
    FT_RAW    = 0x24,
    FT_REPLY  = 0x25,
    FT_LOG    = 0x26,
};
#define FT_IS_UPSTREAM(t)  (((t) & 0xF0u) == 0x20u)

enum { KIND_TILE = 0, KIND_HUB = 1 };

/* A tile's ports, in the order HELLO and INFO use. The hub's one port is 0. */
enum { PORT_W = 0, PORT_N = 1, PORT_E = 2, PORT_S = 3, TILE_PORTS = 4 };
#define PORT_NONE          0xFFu

#define ID_NONE            0u
#define ID_ALL             0xFFFFFFFFu

/* ---- cable speeds ---- */
/* The codes HELLO and INFO use. Every port starts at RATE_BASE; two ports
 * that both can go faster agree on it (DESIGN.md, "How fast"). */
enum { RATE_1M = 0, RATE_4M = 1, RATE_COUNT = 2 };
#define RATE_BASE          RATE_1M
#define RATE_MAX           RATE_4M      /* the fastest this program's ports offer */

static inline uint32_t rate_baud(uint8_t rate)
{
    return rate == RATE_4M ? 4000000u : 1000000u;
}

/* ---- payload sizes ---- */
#define HELLO_MIN_LEN      7            /* a program from before the two speeds says this much */
#define HELLO_LEN          8
/* HELLO's last byte. Without it: 0. */
#define HELLO_CAN(b)       ((b) & 0x03u)            /* the fastest rate the sender's port offers */
#define HELLO_NOW(b)       (((b) >> 2) & 0x03u)     /* the rate this HELLO is sent at */
#define HELLO_BACK         0x20u        /* at the base rate again after a try: answer with a HELLO */
#define HELLO_ASK          0x40u        /* answer this: at the base rate with GO, else with a HELLO */
#define HELLO_GO           0x80u        /* the last thing said at this rate: the faster one is next */
#define BEACON_LEN         8
#define CMD_HEAD_LEN       6            /* target, counter, op */
#define CMD_MAX_ARGS       (FRAME_MAX_PAYLOAD - CMD_HEAD_LEN)
#define REPLY_MAX_DATA     18           /* what follows the counter and the status */
#define UP_HEAD_LEN        4            /* the writer's ID */
#define INFO_MIN_LEN       (UP_HEAD_LEN + 33)   /* without the last two bytes, about the rates */
#define INFO_LEN           (UP_HEAD_LEN + 35)
/* INFO's last two bytes hold two bits for each port, port 0 lowest: the
 * port's rate, and why it is not the fastest. */
enum {
    SLOW_NOT = 0,           /* it is, or nobody is there, or the asking is still to come */
    SLOW_NBR = 1,           /* the neighbour offers no more */
    SLOW_RETRY = 2,         /* a try failed: the next comes in a while */
    SLOW_GAVE_UP = 3,       /* RATE_TRIES tries failed: no more with this neighbour */
};
#define STATE_LEN          (UP_HEAD_LEN + 6)
#define NOTE_LEN           (UP_HEAD_LEN + 4)
#define POS_MAX_PAIRS      28
#define RAW_MAX_VALUES     26

/* ---- commands ---- */
enum {
    OP_PING = 1, OP_GET = 2, OP_SET = 3, OP_SAVE = 4, OP_CAL = 5,
    OP_RAW = 6, OP_REBOOT = 7, OP_BOOTLOADER = 8, OP_QUIET = 9,
    /* firmware update (fw.h); only for one tile at a time, never for all */
    OP_FW_INFO = 10, OP_FW_BEGIN = 11, OP_FW_WRITE = 12, OP_FW_COMMIT = 13, OP_FW_CONFIRM = 14,
};
enum { CAL_STOP = 0, CAL_START = 1, CAL_FORGET = 2 };

/* REPLY status */
enum {
    ST_OK = 0,
    ST_UNKNOWN = 1,     /* no such operation or parameter */
    ST_BAD_ARG = 2,     /* argument missing or out of range */
    ST_FAILED = 3,      /* the tile tried and could not (flash write) */
    ST_BUSY = 4,
};

/* INFO flags */
#define INFO_CALIBRATED    0x01u        /* every key's bottom has been measured */
#define INFO_CALIBRATING   0x02u
#define INFO_RAW           0x04u
#define INFO_SLOT_B        0x08u        /* the program runs from slot B (fw.h) */
#define INFO_TRIAL         0x10u        /* a new program on trial: confirm it, or it goes */
#define INFO_STAGED        0x20u        /* a new program waits for the next restart */

/* ---- times, in milliseconds ---- */
#define HELLO_PERIOD_MS    100
#define DWELL_MIN_MS       250
#define DWELL_MAX_MS       500
#define LINK_UP_MS         350
#define RX_GAP_MS          3      /* a frame stalled this long is thrown away */

#define RATE_HOLD_MS       5      /* after asking: this long for the GO, before asking where it is */
#define RATE_FRESH_MS      3      /* a program that stood still this long does not answer with GO:
                                   * what it was asked may be older than RATE_HOLD_MS */
#define RATE_GUARD_MS      2      /* after a GO: this long for the other end to change its rate */
#define RATE_CALL_MS       2      /* ... then a HELLO this often, until both ends know they are heard */
#define RATE_TRY_MS        100    /* not that far this long after the change: back to the base rate */
#define RATE_BACK_MS       150    /* ... and holding frames back there at most this long, for the
                                   * other end to say it is back too: longer than its RATE_TRY_MS */
#define RATE_RETRY_MS      2000   /* after a try that failed, before the next */
#define RATE_PROVEN_MS     1000   /* a faster link that lasted this long worked */
#define RATE_TRIES         4      /* tries in a row that failed with one neighbour: no more */

#define BEACON_PERIOD_MS   50
#define HUB_START_MS       750    /* a hub's first beacon: after every tile forgot the last hub */
#define PARENT_TIMEOUT_MS  200
#define SEQ_FORGET_MS      500

#define STATE_PERIOD_MS    100
#define INFO_PERIOD_MS     1000
#define TILE_SILENT_MS     500    /* hub: release a tile's keys */
#define TILE_FORGET_MS     3000   /* hub: drop the tile from the table */

#define CMD_RETRY_MS       100
#define CMD_TRIES          5
#define QUIET_LEAD_MS      20     /* QUIET takes effect this long after it arrives */
#define ACTION_DELAY_MS    250    /* REBOOT / BOOTLOADER wait this long: the reply gets out, and
                                     a repeat of the command still finds the tile there */
#define BCAST_REPEATS      3      /* a command for every tile is sent this often ... */
#define BCAST_GAP_MS       5      /* ... this far apart, since nobody answers it */

#endif
