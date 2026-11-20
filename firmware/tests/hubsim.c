/* The hub's program with tiles on its cable, behind a pipe.
 *
 * For tests/test_flash_tile.py, which plays the computer and one tile's chip
 * (its ROM bootloader and its flash), and for tests/test_update.py, which
 * plays the computer. What runs here is ours: the hub of core/ with its
 * console, the tiles of core/, the cables between them and every node's
 * program flash with core/'s bootloader choice in front of it (sim/).
 * Between the serial port and core/ sit copies of the two loops of
 * hub/main.c that are in the way of a flashing program, console_in() and
 * bridge_run(), without the UART and USB calls. They are copies: when
 * hub/main.c changes, change them here.
 *
 * One line in, one line out. A node is a tile's number, or h for the hub.
 * Before the first `s`, in this order:
 *   n <count>    so many tiles (one if not said). The answer: ok and their IDs
 *   w <W|N|E|S>  tile 0's port that the hub's cable is in (W if not said)
 *   o            the hub and every tile come with the program from before
 *                the cables had two rates (sim.h: SIM_ACT_OLD), which calls
 *                itself version 0001, and not with the one this PC builds
 *   k <tile> <port> <tile> <port>
 *                a cable between two tiles. The hub's cable is cable 0, these
 *                are 1, 2, ... in the order they come
 * and at any time:
 *   t <hex>      bytes from the computer
 *   c <hex>      bytes that arrive on the hub's cable (the ROM's answers);
 *                they count only while the bridge is on
 *   d <0|1>      the computer's DTR line
 *   e <0|1>      1: from now on the tiles have their power through the hub, as
 *                on the real boards (sim.h: hub_feeds): none has any while the
 *                hub's program does not run, nor for a moment after it starts.
 *                0, and as this starts: every node has power of its own
 *   p <0|1> [node]
 *                the node (tile 0 if not said): 0 stops it dead, 1 starts it
 *                afresh from what its program flash holds
 *   f <node> -   the node's program flash: all of it erased
 *   f <node> <offset> <hex>
 *                bytes put into it, as a programmer on the debug pads would;
 *                <offset> in hex, from the chip's first byte
 *   r <node> <offset> <count>
 *                the answer: so many bytes of it, as hex
 *   x <node> <n> the n-th erase or write of its program flash from now does
 *                half its work and fails (0: none does)
 *   u <node>     until its next start the node's program knows nothing of
 *                slots, as one from before there was a bootloader: it takes
 *                no new program and cannot say what it runs
 *   l <cable> <frames in a thousand>
 *                what the cable loses, either way
 *   q <node>     the answer: the slot it runs from (a, b, -), whether that is
 *                a trial, what it does (run; rom: in its ROM bootloader; hang;
 *                off), then for slot A and for slot B the trailer's state and
 *                the image's version and checksum (- -: no whole image), then
 *                how often its program restarted it, its watchdog did, and it
 *                found no program to start
 *   s [count]    a millisecond passes; with a count up to so many, of which
 *                the first that has something to tell is the last. The
 *                answer, seven words:
 *                  how many frames the hub has sent on its cable so far
 *                  tile 0: run, rom (its program went into the ROM, or it
 *                    has none), off
 *                  the bridge: -, soon, ending, or <baud>,<parity>,<way>
 *                  bytes for the computer, as hex, or -
 *                  bytes the bridge sent on the cable, as hex, or -
 *                  the hub: how often its program has started, or - while it
 *                    does not run. A hub that does not run has no serial
 *                    port: what the computer sends is lost, and after the
 *                    next start it has to raise DTR again
 *                  how many milliseconds passed
 * The first line it prints: hubsim <tile 0's ID> <its firmware version>.
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

static uint16_t loss0;          /* what a test has cable 0 lose */
static unsigned hub_starts;     /* how often the hub's program has started */
static bool hub_was_up;

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
    s.cable[0].loss[0] = s.cable[0].loss[1] = on ? 1000 : loss0;
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

/* ---- the hub's program stops and starts ---- */

/* Its USB is the program's: no program, no serial port. */
static bool hub_up(void)
{
    const sim_node_t *n = &s.hub.n;

    return n->powered && !n->halted && n->act != SIM_ACT_HANG;
}

/* With the program went whatever it was doing, and what was on its way to it. */
static void hub_gone(void)
{
    if (bridge == BRIDGE_ON)
        cable_garbles(false);
    bridge = BRIDGE_OFF;
    bridge_used = bridge_skip_lf = false;
    dtr_was = closed = false;
    typed_at = typed_len = 0;
    heard_len = 0;
    s.ntext = 0;
}

static void millisecond(void)
{
    uint32_t now = sim_now(&s, SIM_HUB);
    unsigned bridges = s.hub.bridges;
    bool up;

    if (hub_up()) {
        if (s.hub.n.act == SIM_ACT_DEAF)
            typed_at = typed_len;       /* a program that hears nothing */
        if (bridge == BRIDGE_OFF)
            console_in(now);
        else
            bridge_run(now);
    }
    sim_run(&s, 1);
    if (s.hub.bridges != bridges)       /* HUB_ACT_BRIDGE came up */
        bridge = BRIDGE_SOON;
    if (bridge == BRIDGE_OFF || bridge == BRIDGE_SOON)
        console_out();

    up = hub_up();
    if (hub_was_up && !up)
        hub_gone();
    if (!hub_was_up && up)
        hub_starts++;
    hub_was_up = up;
}

/* ---- the pipe ---- */

static void fail(const char *why)
{
    fprintf(stderr, "hubsim: %s\n", why);
    exit(2);
}

static int digit(char c)
{
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1;
}

static size_t unhex(const char *text, uint8_t *out, size_t room)
{
    size_t n = 0;

    while (digit(text[0]) >= 0 && digit(text[1]) >= 0) {
        if (n == room)
            fail("too many bytes at once");
        out[n++] = (uint8_t)(digit(text[0]) << 4 | digit(text[1]));
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

/* A number at *arg, and *arg moved on to the next word. */
static unsigned long number(const char **arg, int base)
{
    char *end;
    unsigned long x = strtoul(*arg, &end, base);

    if (end == *arg || (*end && *end != ' '))
        fail("a number was expected");
    while (*end == ' ')
        end++;
    *arg = end;
    return x;
}

/* The node named at *arg: SIM_HUB or a tile's number. */
static int node_named(const char **arg)
{
    unsigned long i;

    if ((*arg)[0] == 'h' && ((*arg)[1] == ' ' || !(*arg)[1])) {
        *arg += (*arg)[1] ? 2 : 1;
        return SIM_HUB;
    }
    i = number(arg, 10);
    if (i >= (unsigned long)s.ntiles)
        fail("no such node");
    return (int)i;
}

static sim_node_t *node_of(int i)
{
    return i == SIM_HUB ? &s.hub.n : &s.tile[i].n;
}

static uint8_t port_named(const char **arg)
{
    static const char ports[] = "WNES";
    const char *p = (*arg)[0] ? strchr(ports, (*arg)[0]) : NULL;

    if (!p || ((*arg)[1] && (*arg)[1] != ' '))
        fail("a port is W, N, E or S");
    *arg += (*arg)[1] ? 2 : 1;
    return (uint8_t)(p - ports);
}

static const char *tile_word(void)
{
    const sim_node_t *tile = &s.tile[0].n;

    return tile->halted ? "rom" : tile->powered ? "run" : "off";
}

static void show_node(const sim_node_t *n)
{
    static const char *const state[] = {
        [FW_NONE] = "none", [FW_NEW] = "new", [FW_TRIED] = "tried", [FW_GOOD] = "good",
        [FW_PARTIAL] = "partial",
    };

    printf("%c %d %s", n->slot == FW_SLOT_A ? 'a' : n->slot == FW_SLOT_B ? 'b' : '-', n->trial,
           !n->powered ? "off" : n->halted ? "rom" : n->act == SIM_ACT_HANG ? "hang" : "run");
    for (uint8_t slot = 0; slot < FW_SLOTS; slot++) {
        fw_image_t image;

        printf(" %s", state[fw_slot_state(n->chip.mem, slot)]);
        if (fw_image_check(n->chip.mem, slot, &image))
            printf(" %04x %08x", (unsigned)image.version, (unsigned)image.crc);
        else
            fputs(" - -", stdout);
    }
    printf(" %u %u %u\n", n->reboots, n->wdt_resets, n->no_program);
}

int main(void)
{
    static char line[2 * BUF + 64];
    static uint8_t bytes[BUF];
    uint8_t port = 0;
    bool cabled = false;
    size_t n;

    setvbuf(stdout, NULL, _IOLBF, 0);
    sim_init(&s, 20261113);
    sim_add_tile(&s);
    hub_was_up = hub_up();
    hub_starts = hub_was_up;
    printf("hubsim %08x %04x\n", (unsigned)s.tile[0].t.node.id, (unsigned)FW_VERSION);

    while (fgets(line, sizeof line, stdin)) {
        const char *arg;

        line[strcspn(line, "\r\n")] = 0;
        arg = line[0] && line[1] == ' ' ? line + 2 : "";

        /* the hub's cable is the first there is */
        if (!cabled && (line[0] == 's' || line[0] == 'k')) {
            sim_cable(&s, SIM_HUB, 0, 0, port, false);
            cabled = true;
        }

        switch (line[0]) {
        case 'n': {
            unsigned long count = number(&arg, 10);

            if (cabled || s.ntiles != 1 || count < 1 || count > SIM_MAX_TILES)
                fail("n: once, before anything else, 1 to 16");
            while ((unsigned long)s.ntiles < count)
                sim_add_tile(&s);
            fputs("ok", stdout);
            for (int i = 0; i < s.ntiles; i++)
                printf(" %08x", (unsigned)s.tile[i].t.node.id);
            putchar('\n');
            break;
        }
        case 'o':
            if (cabled)
                fail("o: before the first k or s");
            for (int i = SIM_HUB; i < s.ntiles; i++)
                sim_factory(&s, i, SIM_ACT_OLD);
            hub_was_up = hub_up();      /* it starts once more, and that is its first start */
            hub_starts = hub_was_up;
            puts("ok");
            break;
        case 'w':
            if (cabled)
                fail("w: before the first k or s");
            port = port_named(&arg);
            puts("ok");
            break;
        case 'k': {
            int a = node_named(&arg);
            uint8_t pa = port_named(&arg);
            int b = node_named(&arg);
            uint8_t pb = port_named(&arg);

            if (a == SIM_HUB || b == SIM_HUB || sim_cable(&s, a, pa, b, pb, false) < 0)
                fail("k: two tiles, and a free port of each");
            puts("ok");
            break;
        }
        case 't':
            n = unhex(arg, bytes, sizeof bytes);
            if (!hub_was_up)
                n = 0;                  /* no hub, no serial port */
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
            if (hub_was_up) {
                if (dtr_was && arg[0] != '1')
                    closed = true;
                dtr_was = arg[0] == '1';
            }
            puts("ok");
            break;
        case 'e':
            if ((arg[0] != '0' && arg[0] != '1') || arg[1])
                fail("e: 0 or 1");
            s.hub_feeds = arg[0] == '1';
            puts("ok");
            break;
        case 'p': {
            bool on = arg[0] == '1';
            int i = 0;

            if (arg[0] != '0' && arg[0] != '1')
                fail("p: 0 or 1, and a node");
            if (arg[1] == ' ') {
                arg += 2;
                i = node_named(&arg);
            }
            sim_power(&s, i, false);
            if (on)
                sim_power(&s, i, true);
            puts("ok");
            break;
        }
        case 'f': {
            sim_node_t *node = node_of(node_named(&arg));

            if (arg[0] == '-') {
                memset(node->chip.mem, 0xFF, sizeof node->chip.mem);
            } else {
                unsigned long at = number(&arg, 16);

                n = unhex(arg, bytes, sizeof bytes);
                if (at > FLASHSIM_LEN || n > FLASHSIM_LEN - at)
                    fail("f: past the end of the flash");
                memcpy(node->chip.mem + at, bytes, n);
            }
            puts("ok");
            break;
        }
        case 'r': {
            const sim_node_t *node = node_of(node_named(&arg));
            unsigned long at = number(&arg, 16);
            unsigned long count = number(&arg, 10);

            if (at > FLASHSIM_LEN || count > FLASHSIM_LEN - at || count > BUF)
                fail("r: past the end of the flash, or too much at once");
            hex(node->chip.mem + at, count);
            putchar('\n');
            break;
        }
        case 'x': {
            sim_node_t *node = node_of(node_named(&arg));

            node->chip.fail_at = (unsigned)number(&arg, 10);
            puts("ok");
            break;
        }
        case 'u': {
            int i = node_named(&arg);

            if (i == SIM_HUB)
                hub_set_flash(&s.hub.h, NULL, FW_NO_SLOT);
            else
                tile_set_flash(&s.tile[i].t, NULL, FW_NO_SLOT);
            puts("ok");
            break;
        }
        case 'l': {
            unsigned long c = number(&arg, 10);
            unsigned long loss = number(&arg, 10);

            if (c >= (unsigned long)s.ncables || loss > 1000)
                fail("l: a cable there is, and 0 to 1000");
            if (c == 0)
                loss0 = (uint16_t)loss;
            if (c != 0 || bridge != BRIDGE_ON)
                s.cable[c].loss[0] = s.cable[c].loss[1] = (uint16_t)loss;
            puts("ok");
            break;
        }
        case 'q':
            show_node(node_of(node_named(&arg)));
            break;
        case 's': {
            unsigned long count = arg[0] ? number(&arg, 10) : 1, ran = 0;
            const char *tile = tile_word();
            unsigned starts = hub_starts;
            bool up = hub_was_up;

            while (ran < count) {
                millisecond();
                ran++;
                if (to_pc_len || to_cable_len || tile_word() != tile || hub_starts != starts
                    || hub_was_up != up)
                    break;
            }
            printf("%u %s ", (unsigned)s.cable[0].sent[0], tile_word());
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
            if (hub_was_up)
                printf(" %u", hub_starts);
            else
                fputs(" -", stdout);
            printf(" %lu\n", ran);
            to_pc_len = to_cable_len = 0;
            break;
        }
        default:
            fail("no such command");
        }
    }
    return 0;
}
