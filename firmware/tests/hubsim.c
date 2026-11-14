/* The hub's program with one tile on its cable, behind a pipe.
 *
 * For tests/test_flash_tile.py, which plays the computer and the tile's
 * chip (its ROM bootloader and its flash). What runs here is ours: the hub
 * of core/ with its console, the tile of core/, and the cable between them
 * (sim/). Between the serial port and core/ sit copies of the two loops of
 * hub/main.c that are in the way of a flashing program, console_in() and
 * bridge_run(), without the UART and USB calls. They are copies: when
 * hub/main.c changes, change them here.
 *
 * One line in, one line out. Before the first `s`:
 *   w <W|N|E|S>  the tile's port that the hub's cable is in (W if not said)
 * and at any time:
 *   t <hex>      bytes from the computer
 *   c <hex>      bytes that arrive on the hub's cable (the ROM's answers);
 *                they count only while the bridge is on
 *   d <0|1>      the computer's DTR line
 *   p <0|1>      the tile: 0 stops it dead, 1 starts its program afresh
 *   s            a millisecond passes. The answer, five words:
 *                  how many frames the hub has sent on its cable so far
 *                  the tile: run, rom (its program went into the ROM), off
 *                  the bridge: -, soon, ending, or <baud>,<parity>,<way>
 *                  bytes for the computer, as hex, or -
 *                  bytes the bridge sent on the cable, as hex, or -
 * The first line it prints: hubsim <the tile's ID> <its firmware version>.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sim.h"

#define BUF 8192

static sim_t s;

/* ---- as hub/main.c and hub/usb.c ---- */

enum { BRIDGE_OFF, BRIDGE_SOON, BRIDGE_ON, BRIDGE_ENDING };

static int bridge;
static bool bridge_used;        /* a byte from the computer went through */
static bool bridge_skip_lf;     /* `bridge` ended with CR: an LF right behind it is not data */
static bool dtr_was, closed;

static uint8_t typed[BUF];      /* from the computer, not taken yet */
static size_t typed_len, typed_at;
static uint8_t heard[BUF];      /* from the cable, for the bridge */
static size_t heard_len;
static uint8_t to_pc[SIM_TEXT_MAX + BUF], to_cable[BUF];
static size_t to_pc_len, to_cable_len;

static bool usb_serial_closed(void)
{
    bool was = closed;

    closed = false;
    return was;
}

/* While the bridge is on the hub's UART runs at another speed: what the
 * tile's program sends, if it is running, is no frame to the hub, and the
 * other way round. */
static void cable_garbles(bool on)
{
    s.cable[0].loss[0] = s.cable[0].loss[1] = on ? 1000 : 0;
}

static void bridge_run(uint32_t now)
{
    if (bridge == BRIDGE_SOON) {
        /* the `ok` has left: the simulator takes the console's text at once */
        usb_serial_closed();            /* a close before now is not about the bridge */
        bridge_used = false;
        bridge = BRIDGE_ON;
        cable_garbles(true);
    }

    if (bridge == BRIDGE_ENDING) {
        typed_at = typed_len = 0;       /* what was on its way to the bridge is nobody's */
        heard_len = 0;
        bridge = BRIDGE_OFF;
        cable_garbles(false);
        hub_bridge_end(&s.hub.h, now);
        return;
    }

    /* computer to cable */
    if (bridge_skip_lf && typed_at < typed_len) {
        if (typed[typed_at] == '\n')
            typed_at++;
        bridge_skip_lf = false;
    }
    if (typed_at < typed_len) {
        memcpy(to_cable + to_cable_len, typed + typed_at, typed_len - typed_at);
        to_cable_len += typed_len - typed_at;
        typed_at = typed_len;
        bridge_used = true;
    }

    /* cable to computer */
    memcpy(to_pc + to_pc_len, heard, heard_len);
    to_pc_len += heard_len;
    heard_len = 0;

    if (usb_serial_closed() && bridge_used)
        bridge = BRIDGE_ENDING;
}

/* What was typed goes to the console one character at a time: a command
 * that talks to tiles makes the rest wait, and what follows `bridge` is
 * the bridge's. */
static void console_in(uint32_t now)
{
    hub_t *h = &s.hub.h;

    if (h->bridging)
        return;
    while (typed_at < typed_len) {
        uint8_t c = typed[typed_at];

        if (!hub_console_rx(h, &c, 1, now))
            return;                     /* busy: offer it again later */
        typed_at++;
        if (h->bridging) {
            bridge_skip_lf = c == '\r';
            return;
        }
    }
}

static void console_out(void)
{
    memcpy(to_pc + to_pc_len, s.text, s.ntext);
    to_pc_len += s.ntext;
    s.ntext = 0;
}

static void millisecond(void)
{
    uint32_t now = sim_now(&s, SIM_HUB);
    unsigned bridges = s.hub.bridges;

    if (bridge == BRIDGE_OFF)
        console_in(now);
    else
        bridge_run(now);
    sim_run(&s, 1);
    if (s.hub.bridges != bridges)       /* HUB_ACT_BRIDGE came up */
        bridge = BRIDGE_SOON;
    if (bridge == BRIDGE_OFF || bridge == BRIDGE_SOON)
        console_out();
}

/* ---- the pipe ---- */

static void fail(const char *why)
{
    fprintf(stderr, "hubsim: %s\n", why);
    exit(2);
}

static size_t unhex(const char *text, uint8_t *out, size_t room)
{
    size_t n = 0;
    unsigned byte;

    while (sscanf(text, "%2x", &byte) == 1) {
        if (n == room)
            fail("too many bytes at once");
        out[n++] = (uint8_t)byte;
        text += 2;
    }
    return n;
}

static void hex(const uint8_t *data, size_t n)
{
    if (!n)
        putchar('-');
    for (size_t i = 0; i < n; i++)
        printf("%02x", data[i]);
}

int main(void)
{
    static char line[2 * BUF + 16];
    static uint8_t bytes[BUF];
    static const char ports[] = "WNES";
    uint8_t port = 0;
    bool cabled = false;
    size_t n;

    setvbuf(stdout, NULL, _IOLBF, 0);
    sim_init(&s, 20261113);
    sim_add_tile(&s);
    printf("hubsim %08x %04x\n", (unsigned)s.tile[0].t.node.id, (unsigned)FW_VERSION);

    while (fgets(line, sizeof line, stdin)) {
        const char *arg = line[0] && line[1] == ' ' ? line + 2 : "";
        const sim_node_t *tile = &s.tile[0].n;

        switch (line[0]) {
        case 'w':
            if (cabled || !arg[0] || !strchr(ports, arg[0]))
                fail("w: a port, before the first s");
            port = (uint8_t)(strchr(ports, arg[0]) - ports);
            puts("ok");
            break;
        case 't':
            n = unhex(arg, bytes, sizeof bytes);
            if (typed_at == typed_len)
                typed_at = typed_len = 0;
            if (typed_len + n > sizeof typed)
                fail("too much typed and not taken");
            memcpy(typed + typed_len, bytes, n);
            typed_len += n;
            puts("ok");
            break;
        case 'c':
            n = unhex(arg, bytes, sizeof bytes);
            if (bridge == BRIDGE_ON) {
                if (heard_len + n > sizeof heard)
                    fail("too much on the cable");
                memcpy(heard + heard_len, bytes, n);
                heard_len += n;
            }
            puts("ok");
            break;
        case 'd':
            /* tud_cdc_line_state_cb() */
            if (dtr_was && arg[0] != '1')
                closed = true;
            dtr_was = arg[0] == '1';
            puts("ok");
            break;
        case 'p':
            sim_power(&s, 0, false);
            if (arg[0] == '1')
                sim_power(&s, 0, true);
            puts("ok");
            break;
        case 's':
            if (!cabled) {
                sim_cable(&s, SIM_HUB, 0, 0, port, false);
                cabled = true;
            }
            millisecond();
            printf("%u %s ", (unsigned)s.cable[0].sent[0],
                   tile->halted ? "rom" : tile->powered ? "run" : "off");
            if (bridge == BRIDGE_ON)
                printf("%u,%c,%s", (unsigned)s.hub.h.bridge.baud, s.hub.h.bridge.parity,
                       s.hub.h.bridge.swapped ? "swapped" : "straight");
            else
                fputs(bridge == BRIDGE_SOON ? "soon" : bridge == BRIDGE_ENDING ? "ending" : "-",
                      stdout);
            putchar(' ');
            hex(to_pc, to_pc_len);
            putchar(' ');
            hex(to_cable, to_cable_len);
            putchar('\n');
            to_pc_len = to_cable_len = 0;
            break;
        default:
            fail("no such command");
        }
    }
    return 0;
}
