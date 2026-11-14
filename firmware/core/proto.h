/* The numbers both programs must agree on: frame types, sizes, times.
 * DESIGN.md explains them; this file is that text as constants. */
#ifndef PIANO_PROTO_H
#define PIANO_PROTO_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define PROTO_VERSION      1
#define FW_VERSION         0x0001u      /* reported in INFO */

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

/* ---- payload sizes ---- */
#define HELLO_LEN          7
#define BEACON_LEN         8
#define CMD_HEAD_LEN       6            /* target, counter, op */
#define UP_HEAD_LEN        4            /* the writer's ID */
#define INFO_LEN           (UP_HEAD_LEN + 33)
#define STATE_LEN          (UP_HEAD_LEN + 6)
#define NOTE_LEN           (UP_HEAD_LEN + 4)
#define POS_MAX_PAIRS      28
#define RAW_MAX_VALUES     26

/* ---- commands ---- */
enum {
    OP_PING = 1, OP_GET = 2, OP_SET = 3, OP_SAVE = 4, OP_CAL = 5,
    OP_RAW = 6, OP_REBOOT = 7, OP_BOOTLOADER = 8, OP_QUIET = 9,
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

/* ---- times, in milliseconds ---- */
#define HELLO_PERIOD_MS    100
#define DWELL_MIN_MS       250
#define DWELL_MAX_MS       500
#define LINK_UP_MS         350
#define RX_GAP_MS          3      /* a frame stalled this long is thrown away */

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
