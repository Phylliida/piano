/* Whole keyboards on the simulator (sim/sim.h): real tile and hub programs,
 * simulated cables, keys, flash and time. Each test builds a keyboard, does
 * what a player or a clumsy hand would do, and checks what the computer gets
 * (MIDI, console text) and what the programs believe. */
#include "sim.h"

#include "notes.h"
#include "t.h"

#include <stdarg.h>

static sim_t s;                         /* several hundred kilobytes: not for the stack */

#define H()   (&s.hub.h)
#define T(i)  (&s.tile[i].t)

/* The hub's entry for simulated tile i, or NULL. */
static hub_tile_t *HT(int i)
{
    return hub_find(H(), T(i)->node.id);
}

static bool alive(int i)
{
    return s.tile[i].n.powered && !s.tile[i].n.halted;
}

/* ------------------------------------------------------- watching cables --- */

#define UP_TYPES   8
#define NOTES_MAX  8192
#define UP(type)   ((type) - 0x20)

typedef struct {
    int tile;
    uint8_t counter, key;
    bool on;
    uint16_t speed;
} seen_note_t;

static struct {
    unsigned made[SIM_MAX_TILES][UP_TYPES];     /* upstream frames a tile sent of its own, by type */
    unsigned up[SIM_MAX_TILES][UP_TYPES];       /* ... and those that reached the hub */
    unsigned own;                       /* upstream frames that came back to the tile that made them */
    unsigned cmd_hub;                   /* CMD frames the hub sent */
    unsigned cmd_parent[SIM_MAX_TILES]; /* CMD frames a tile got on its parent port ... */
    unsigned cmd_other[SIM_MAX_TILES];  /* ... and on its other ports */
    uint8_t seen[SIM_MAX_TILES][256];   /* NOTE frames at the hub, by the tile's counter */
    unsigned dups;                      /* NOTE frames that reached the hub a second time */
    seen_note_t note[NOTES_MAX];        /* every NOTE frame that reached the hub, in order */
    int nnotes;
} tap;

static void tap_clear(void)
{
    memset(&tap, 0, sizeof tap);
}

static int tile_by_id(uint32_t id)
{
    for (int i = 0; i < s.ntiles; i++) {
        if (T(i)->node.id == id)
            return i;
    }
    return -1;
}

static void on_tap(void *ctx, int from, uint8_t from_port, int to, uint8_t to_port,
                   uint8_t type, const uint8_t *pl, uint8_t len)
{
    (void)ctx;
    (void)from_port;
    if (FT_IS_UPSTREAM(type) && len >= UP_HEAD_LEN && UP(type) < UP_TYPES) {
        int who = tile_by_id(get32(pl));
        if (who < 0)
            return;
        if (from == who)
            tap.made[who][UP(type)]++;
        if (to == who)
            tap.own++;
        if (to != SIM_HUB)
            return;
        tap.up[who][UP(type)]++;
        if (type == FT_NOTE && len >= NOTE_LEN) {
            const uint8_t *b = pl + UP_HEAD_LEN;
            if (tap.seen[who][b[0]]++)
                tap.dups++;
            if (tap.nnotes < NOTES_MAX) {
                seen_note_t *n = &tap.note[tap.nnotes++];
                n->tile = who;
                n->counter = b[0];
                n->key = b[1] & 0x7Fu;
                n->on = (b[1] & 0x80u) != 0;
                n->speed = get16(b + 2);
            }
        }
    } else if (type == FT_CMD) {
        if (from == SIM_HUB)
            tap.cmd_hub++;
        if (to >= 0) {
            if (to_port == T(to)->node.parent)
                tap.cmd_parent[to]++;
            else
                tap.cmd_other[to]++;
        }
    }
}

/* No upstream frame reached the hub more often than its tile sent it. */
static bool tap_no_extra(void)
{
    for (int i = 0; i < s.ntiles; i++) {
        for (int t = 0; t < UP_TYPES; t++) {
            if (tap.up[i][t] > tap.made[i][t])
                return false;
        }
    }
    return true;
}

/* Frames that have left any end of any cable. */
static unsigned long frames_sent(void)
{
    unsigned long n = 0;

    for (int c = 0; c < s.ncables; c++)
        n += s.cable[c].sent[0] + s.cable[c].sent[1];
    return n;
}

/* --------------------------------------------------------------- building --- */

static int hubcab;                      /* the hub's cable; its end 0 is the hub */
static int gw, gh;
static int place_u[SIM_MAX_TILES], place_v[SIM_MAX_TILES];     /* where each tile was put */
static int ecab[4][4], ncab[4][4];      /* the cable east of, and north of, tile (u, v) */

static void start(uint32_t seed)
{
    sim_init(&s, seed);
    s.tap = on_tap;
    tap_clear();
    hubcab = -1;
    gw = gh = 0;
}

/* w x h tiles, tile (u, v) being number u + w * v, cabled to all their
 * neighbours; the hub on the east port of the bottom right one, where it
 * snaps on. `crossed` has a bit for each cable in the order they are made. */
static void grid(int w, int h, uint32_t crossed)
{
    int bit = 0;

    gw = w;
    gh = h;
    for (int i = 0; i < w * h; i++) {
        int t = sim_add_tile(&s);
        place_u[t] = i % w;
        place_v[t] = i / w;
    }
    for (int v = 0; v < h; v++) {
        for (int u = 0; u < w; u++) {
            int i = u + w * v;
            ecab[u][v] = ncab[u][v] = -1;
            if (u + 1 < w)
                ecab[u][v] = sim_cable(&s, i, PORT_E, i + 1, PORT_W, (crossed >> bit++) & 1u);
            if (v + 1 < h)
                ncab[u][v] = sim_cable(&s, i, PORT_N, i + w, PORT_S, (crossed >> bit++) & 1u);
        }
    }
    hubcab = sim_cable(&s, SIM_HUB, 0, w - 1, PORT_E, (crossed >> bit) & 1u);
}

/* ------------------------------------------------------ what should be so --- */

static int dist[SIM_MAX_TILES];         /* cables between a tile and the hub's tile; -1: no way */
static int min_u, min_v;                /* the lowest u and v among the tiles with a way */

/* Work those out from the cables that are plugged in and the tiles that run. */
static void distances(void)
{
    int todo[SIM_MAX_TILES], head = 0, count = 0;
    const sim_port_t *hp = &s.hub.port[0];

    for (int i = 0; i < s.ntiles; i++)
        dist[i] = -1;
    if (hp->cable >= 0 && s.cable[hp->cable].plugged) {
        int root = s.cable[hp->cable].node[!hp->end];
        if (alive(root)) {
            dist[root] = 0;
            todo[count++] = root;
        }
    }
    while (head < count) {
        int i = todo[head++];
        for (uint8_t p = 0; p < TILE_PORTS; p++) {
            const sim_port_t *sp = &s.tile[i].port[p];
            if (sp->cable < 0 || !s.cable[sp->cable].plugged)
                continue;
            int j = s.cable[sp->cable].node[!sp->end];
            if (j < 0 || !alive(j) || dist[j] >= 0)
                continue;
            dist[j] = dist[i] + 1;
            todo[count++] = j;
        }
    }
    min_u = min_v = 99;
    for (int i = 0; i < s.ntiles; i++) {
        if (dist[i] < 0)
            continue;
        if (place_u[i] < min_u) min_u = place_u[i];
        if (place_v[i] < min_v) min_v = place_v[i];
    }
}

/* Following parents from tile i ends at the hub, and soon. */
static bool reaches_hub(int i)
{
    for (int step = 0; step <= s.ntiles; step++) {
        i = sim_toward_hub(&s, i);
        if (i == SIM_HUB)
            return true;
        if (i < 0 || !alive(i))
            return false;
    }
    return false;                       /* round in a circle */
}

/* The keyboard has settled: every tile that has a way to the hub uses a
 * shortest one, and the hub has it on the grid where it lies on the table.
 * Tiles with no way know they have none. */
static void check_settled(void)
{
    distances();
    for (int i = 0; i < s.ntiles; i++) {
        if (!alive(i))
            continue;
        if (dist[i] < 0) {
            CHECK_EQ(T(i)->node.parent, PORT_NONE);
            continue;
        }
        CHECK(reaches_hub(i));
        CHECK_EQ(T(i)->node.parent_hops, dist[i]);

        const hub_tile_t *ht = HT(i);
        CHECK(ht != NULL);
        if (!ht)
            continue;
        CHECK(!ht->silent);
        CHECK(ht->placed);
        CHECK_EQ(ht->u, place_u[i] - min_u);
        CHECK_EQ(ht->v, place_v[i] - min_v);
        CHECK_EQ(ht->hops, dist[i]);
    }
    CHECK_EQ(H()->nproblems, 0);
}

/* The MIDI note a key should give, the keyboard being as it is now. */
static int pitch(int tile, int key)
{
    distances();
    return notes_note(&H()->set, place_u[tile] - min_u, place_v[tile] - min_v, (uint8_t)key);
}

/* ------------------------------------------------------------ the console --- */

/* Is there a line that is exactly `want` in what the hub printed? */
static bool has_line(const char *want)
{
    size_t n = strlen(want);

    for (const char *p = s.text, *e; (e = strstr(p, "\r\n")) != NULL; p = e + 2) {
        if ((size_t)(e - p) == n && !strncmp(p, want, n))
            return true;
    }
    return false;
}

static bool has_linef(const char *f, ...) __attribute__((format(printf, 1, 2)));
static bool has_linef(const char *f, ...)
{
    char line[160];
    va_list ap;

    va_start(ap, f);
    vsnprintf(line, sizeof line, f, ap);
    va_end(ap);
    return has_line(line);
}

static bool has_error(void)
{
    for (const char *p = s.text, *e; (e = strstr(p, "\r\n")) != NULL; p = e + 2) {
        if (!strncmp(p, "error: ", 7))
            return true;
    }
    return false;
}

/* Lines that start with `head`. */
static int lines_with(const char *head)
{
    size_t n = strlen(head);
    int count = 0;

    for (const char *p = s.text, *e; (e = strstr(p, "\r\n")) != NULL; p = e + 2) {
        if ((size_t)(e - p) >= n && !strncmp(p, head, n))
            count++;
    }
    return count;
}

/* Type a command and wait for its `ok` or `error`. Returns everything the
 * hub printed meanwhile; has_line() and friends look at the same text. */
static const char *say(const char *f, ...) __attribute__((format(printf, 1, 2)));
static const char *say(const char *f, ...)
{
    char line[128];
    va_list ap;

    va_start(ap, f);
    vsnprintf(line, sizeof line, f, ap);
    va_end(ap);

    s.ntext = 0;
    s.text[0] = 0;
    sim_type(&s, line);
    sim_type(&s, "\r");
    for (int ms = 0; ms < 8000 && !has_line("ok") && !has_error(); ms++)
        sim_run(&s, 1);
    return s.text;
}

/* That, and the answer has to be a plain `ok`. */
#define SAY_OK(...)  do { say(__VA_ARGS__); CHECK(has_line("ok")); CHECK(!has_error()); } while (0)

/* ------------------------------------------------------------------- MIDI --- */

static void midi_clear(void)
{
    s.nmidi = 0;
}

/* Messages of one kind (0x90: note on, 0x80: off, 0xA0: aftertouch) for one
 * note; note < 0: for any. */
static int midi_count(uint8_t kind, int note)
{
    int n = 0;

    for (int i = 0; i < s.nmidi; i++) {
        if ((s.midi[i][0] & 0xF0u) == kind && (note < 0 || s.midi[i][1] == note))
            n++;
    }
    return n;
}

/* The last such message's second data byte (velocity, pressure); -1: none. */
static int midi_last(uint8_t kind, int note)
{
    for (int i = s.nmidi - 1; i >= 0; i--) {
        if ((s.midi[i][0] & 0xF0u) == kind && (note < 0 || s.midi[i][1] == note))
            return s.midi[i][2];
    }
    return -1;
}

/* How many notes sound after all the messages so far. -1 if a note was
 * started twice without a stop between, or stopped when it was not on: a
 * synth would not mind, but the hub should never send that. */
static int midi_sounding(void)
{
    bool on[128] = { false };
    int n = 0;

    if (s.nmidi == SIM_MIDI_MAX)
        return -1;                      /* the log is full: not everything is in it */
    for (int i = 0; i < s.nmidi; i++) {
        uint8_t kind = s.midi[i][0] & 0xF0u, note = s.midi[i][1] & 0x7Fu;
        if (kind == 0x90) {
            if (on[note])
                return -1;
            on[note] = true;
        } else if (kind == 0x80) {
            if (!on[note])
                return -1;
            on[note] = false;
        }
    }
    for (int i = 0; i < 128; i++)
        n += on[i];
    return n;
}

/* Notes the hub believes are sounding. */
static int hub_holding(void)
{
    int n = 0;

    for (int i = 0; i < 128; i++)
        n += H()->held[i] != 0;
    return n;
}

/* ------------------------------------------------------------------- keys --- */

#define FLOOR  65535

static void press(int tile, int key, uint32_t ms)
{
    sim_move(&s, tile, key, FLOOR, ms);
}

static void lift(int tile, int key, uint32_t ms)
{
    sim_move(&s, tile, key, 0, ms);
}

/* Somebody plays for `ms`: on every tile `key` goes down and up again
 * every 400 ms, the tiles one after another. Then all of them are let go
 * and the last notes get time to arrive. */
static void play(uint32_t ms, int key)
{
    for (uint32_t t = 0; t < ms; t++) {
        for (int i = 0; i < s.ntiles; i++) {
            uint32_t at = (t + 400u - 40u * (uint32_t)i) % 400u;
            if (at == 0)
                press(i, key, 10);
            else if (at == 120)
                lift(i, key, 10);
        }
        sim_run(&s, 1);
    }
    for (int i = 0; i < s.ntiles; i++)
        lift(i, key, 10);
    sim_run(&s, 350);
}

/* ============================================================== the tests === */

/* One tile on the hub, with either kind of cable. */
static void one_tile(bool crossed)
{
    start(crossed ? 11 : 12);
    grid(1, 1, crossed);
    sim_run(&s, 3000);
    check_settled();
    CHECK_EQ(H()->width, 1);
    CHECK_EQ(H()->height, 1);
    CHECK_EQ(hub_root(H()), T(0)->node.id);
    /* the two ends found the way round that works for this cable */
    CHECK_EQ(s.hub.port[0].swapped != s.tile[0].port[PORT_E].swapped, !crossed);
    /* the tile's other ports go on looking and find nobody */
    CHECK(!T(0)->node.port[PORT_W].link.up);
    CHECK(!T(0)->node.port[PORT_N].link.up);
    CHECK(!T(0)->node.port[PORT_S].link.up);

    /* the south-west key is C3; one up is a fifth above */
    midi_clear();
    press(0, 0, 20);
    sim_run(&s, 100);
    CHECK_EQ(midi_count(0x90, 48), 1);
    CHECK_EQ(midi_count(0x90, -1), 1);
    lift(0, 0, 20);
    press(0, 1, 20);
    sim_run(&s, 100);
    CHECK_EQ(midi_count(0x80, 48), 1);
    CHECK_EQ(midi_count(0x90, 55), 1);
    lift(0, 1, 20);
    sim_run(&s, 100);
    CHECK_EQ(midi_sounding(), 0);
    CHECK_EQ(hub_holding(), 0);

    SAY_OK("tiles");
    CHECK(has_line("1 tile"));
    SAY_OK("grid");
    CHECK(has_line("hub on tile 0"));
    CHECK(has_line("1 x 1"));
    CHECK_EQ(tap.dups, 0);
    CHECK_EQ(tap.own, 0);
}

static void one_tile_crossed_cable(void)
{
    one_tile(true);
}

static void one_tile_straight_cable(void)
{
    one_tile(false);
}

/* Four tiles in a row, cables of both kinds between them. */
static void row_with_mixed_cables(void)
{
    start(21);
    grid(4, 1, 0x0A);           /* straight, crossed, straight; the hub's is crossed */
    sim_run(&s, 3500);
    check_settled();
    CHECK_EQ(H()->width, 4);
    CHECK_EQ(H()->height, 1);
    CHECK_EQ(T(0)->node.parent, PORT_E);
    CHECK_EQ(T(0)->node.parent_hops, 3);

    /* every tile's south-west key: a major third higher from tile to tile */
    midi_clear();
    for (int i = 0; i < 4; i++) {
        CHECK_EQ(pitch(i, 0), 48 + 4 * i);
        press(i, 0, 20);
        sim_run(&s, 100);
        CHECK_EQ(midi_count(0x90, 48 + 4 * i), 1);
        lift(i, 0, 20);
        sim_run(&s, 100);
        CHECK_EQ(midi_count(0x80, 48 + 4 * i), 1);
    }
    CHECK_EQ(midi_count(0x90, -1), 4);
    CHECK_EQ(midi_sounding(), 0);
    CHECK_EQ(tap.dups, 0);
    CHECK_EQ(tap.own, 0);
    CHECK(tap_no_extra());
}

/* Nine tiles in a square, every neighbour cabled to every neighbour: twelve
 * cables where eight would do. The tiles make a tree of it. */
static void square_of_nine(void)
{
    start(31);
    grid(3, 3, 0x0B5A);
    sim_run(&s, 4000);
    check_settled();
    CHECK_EQ(H()->width, 3);
    CHECK_EQ(H()->height, 3);
    CHECK_EQ(T(0)->node.parent_hops, 2);        /* (0, 0): two tiles from the hub's */
    CHECK_EQ(T(6)->node.parent_hops, 4);        /* (0, 2): the far corner */

    SAY_OK("tiles");
    CHECK(has_line("9 tiles"));
    SAY_OK("grid");
    CHECK(has_line("3 x 3"));
    CHECK_EQ(lines_with("problem"), 0);
    CHECK_EQ(lines_with("unplaced"), 0);

    /* A quiet second, every tile's south-west key played (the top row's
     * other keys are above MIDI's highest note): nothing comes back to its
     * maker, nothing arrives twice. */
    tap_clear();
    midi_clear();
    for (int i = 0; i < 9; i++)
        press(i, 0, 15);
    sim_run(&s, 500);
    for (int i = 0; i < 9; i++)
        lift(i, 0, 15);
    sim_run(&s, 500);
    CHECK_EQ(tap.own, 0);
    CHECK_EQ(tap.dups, 0);
    CHECK(tap_no_extra());
    CHECK_EQ(tap.nnotes, 18);
    for (int i = 0; i < 9; i++) {
        CHECK_EQ(tap.up[i][UP(FT_NOTE)], 2);
        CHECK_EQ(pitch(i, 0), 48 + 4 * place_u[i] + 35 * place_v[i]);
        CHECK_EQ(midi_count(0x90, pitch(i, 0)), 1);
        CHECK_EQ(midi_count(0x80, pitch(i, 0)), 1);
        CHECK(tap.up[i][UP(FT_STATE)] >= 9);
        CHECK(tap.up[i][UP(FT_INFO)] >= 1);
    }
    CHECK_EQ(midi_sounding(), 0);
    CHECK_EQ(H()->stats.notes_lost, 0);
    CHECK_EQ(H()->stats.notes_old, 0);
    CHECK_EQ(H()->stats.healed_on + H()->stats.healed_off, 0);

    /* The top right key of the top right tile would be far above MIDI's
     * highest note: the hub follows it and sends nothing. */
    midi_clear();
    CHECK_EQ(pitch(8, 39), -1);
    press(8, 39, 15);
    sim_run(&s, 150);
    CHECK(KEY_BIT(HT(8)->on, 39));
    lift(8, 39, 15);
    sim_run(&s, 150);
    CHECK(!KEY_BIT(HT(8)->on, 39));
    CHECK_EQ(s.nmidi, 0);

    /* A command for each tile: nine frames from the hub, and each tile gets
     * each of them once from its parent, whoever it is for. */
    tap_clear();
    SAY_OK("ping all");
    CHECK_EQ(lines_with("tile "), 9);
    CHECK_EQ(tap.cmd_hub, 9);
    for (int i = 0; i < 9; i++) {
        CHECK_EQ(tap.cmd_parent[i], 9);
        CHECK_EQ(tap.up[i][UP(FT_REPLY)], 1);
    }
    CHECK_EQ(H()->stats.no_answer, 0);
}

/* Cables come out and go back in while somebody plays. One key far from the
 * hub stays down through all of it and must sound through all of it. */
static void cables_pulled_while_playing(void)
{
    static const struct {
        int u, v;
        bool north;
    } pull[] = {
        { 1, 0, false },        /* between the hub's tile and its west neighbour */
        { 2, 0, true },         /* the hub's tile's other cable */
        { 0, 0, false },        /* the held key's tile loses its way east */
        { 1, 1, true },         /* in the middle */
        { 0, 1, true },         /* a cable the tree may not even use */
    };

    start(41);
    grid(3, 3, 0x1A53);
    sim_run(&s, 4000);
    check_settled();

    midi_clear();
    tap_clear();
    int held = pitch(0, 12);
    press(0, 12, 20);
    sim_run(&s, 200);
    CHECK_EQ(midi_count(0x90, held), 1);

    for (size_t i = 0; i < sizeof pull / sizeof pull[0]; i++) {
        int c = pull[i].north ? ncab[pull[i].u][pull[i].v] : ecab[pull[i].u][pull[i].v];

        sim_unplug(&s, c);
        play(1500, 0);
        check_settled();        /* a tree again, by the shortest ways there are now */
        CHECK_EQ(H()->width, 3);
        CHECK_EQ(H()->height, 3);
        CHECK_EQ(midi_sounding(), 1);

        sim_plug(&s, c);
        play(1500, 0);
        check_settled();        /* and the shortest ways are the old ones again */
        CHECK_EQ(midi_sounding(), 1);

        /* the held key never stopped */
        CHECK_EQ(midi_count(0x90, held), 1);
        CHECK_EQ(midi_count(0x80, held), 0);
    }
    CHECK_EQ(hub_holding(), 1);
    CHECK_EQ(H()->stats.silent, 0);     /* no tile was ever out of touch for half a second */
    CHECK_EQ(tap.dups, 0);
    CHECK(tap_no_extra());

    lift(0, 12, 20);
    sim_run(&s, 200);
    CHECK_EQ(midi_count(0x80, held), 1);
    CHECK_EQ(midi_sounding(), 0);
}

/* The hub's own cable comes out: every note stops. Back in: the keys still
 * down sound again. */
static void hub_cable_pulled(void)
{
    start(43);
    grid(2, 2, 0x06);
    sim_run(&s, 3500);
    check_settled();

    midi_clear();
    int a = pitch(0, 12), b = pitch(3, 30);
    press(0, 12, 20);
    press(3, 30, 20);
    sim_run(&s, 200);
    CHECK_EQ(midi_sounding(), 2);

    sim_unplug(&s, hubcab);
    sim_run(&s, 1000);
    CHECK_EQ(midi_count(0x80, a), 1);
    CHECK_EQ(midi_count(0x80, b), 1);
    CHECK_EQ(midi_sounding(), 0);
    CHECK_EQ(hub_holding(), 0);
    check_settled();                    /* nobody has a way, and every tile knows */
    CHECK_EQ(hub_root(H()), ID_NONE);

    sim_plug(&s, hubcab);
    sim_run(&s, 2500);
    check_settled();
    /* the notes come back at a middling strength: nobody saw how hard they were hit */
    CHECK_EQ(midi_count(0x90, a), 2);
    CHECK_EQ(midi_count(0x90, b), 2);
    CHECK_EQ(midi_last(0x90, a), 64);
    CHECK_EQ(midi_sounding(), 2);

    lift(0, 12, 20);
    lift(3, 30, 20);
    sim_run(&s, 200);
    CHECK_EQ(midi_sounding(), 0);
    CHECK_EQ(tap.dups, 0);
}

/* A column of the keyboard is pulled off: its notes stop, later the hub
 * forgets its tiles and the grid is the two columns left. Put back, it is
 * the old keyboard again. */
static void column_pulled_off(void)
{
    start(45);
    grid(3, 3, 0x0F0F);
    sim_run(&s, 4000);
    check_settled();

    midi_clear();
    int far = pitch(3, 12), near = pitch(5, 12);
    press(3, 12, 20);           /* (0, 1), about to go */
    press(5, 12, 20);           /* (2, 1), staying */
    sim_run(&s, 200);
    CHECK_EQ(midi_sounding(), 2);

    for (int v = 0; v < 3; v++)
        sim_unplug(&s, ecab[0][v]);
    sim_run(&s, 800);
    CHECK(HT(3) != NULL && HT(3)->silent);
    CHECK_EQ(midi_count(0x80, far), 1);
    CHECK_EQ(midi_count(0x80, near), 0);
    CHECK_EQ(midi_sounding(), 1);

    sim_run(&s, 3000);
    CHECK(HT(0) == NULL);
    CHECK(HT(3) == NULL);
    CHECK(HT(6) == NULL);
    check_settled();            /* the six that are left, one column further west now */
    CHECK_EQ(H()->width, 2);
    CHECK_EQ(H()->height, 3);
    CHECK_EQ(HT(5)->u, 1);
    SAY_OK("tiles");
    CHECK(has_line("6 tiles"));

    /* The staying key keeps the note it started with. Pressed again it is
     * a key of the smaller keyboard: 8 columns further west, 4 steps lower. */
    CHECK_EQ(midi_count(0x80, near), 0);
    lift(5, 12, 20);
    sim_run(&s, 150);
    CHECK_EQ(midi_count(0x80, near), 1);
    press(5, 12, 20);
    sim_run(&s, 150);
    CHECK_EQ(pitch(5, 12), near - 4);
    CHECK_EQ(midi_count(0x90, near - 4), 1);
    lift(5, 12, 20);
    sim_run(&s, 150);

    for (int v = 0; v < 3; v++)
        sim_plug(&s, ecab[0][v]);
    sim_run(&s, 2500);
    check_settled();
    CHECK_EQ(H()->width, 3);
    /* (0, 1)'s key was down all along */
    CHECK_EQ(midi_count(0x90, far), 2);
    CHECK_EQ(midi_last(0x90, far), 64);
    lift(3, 12, 20);
    sim_run(&s, 200);
    CHECK_EQ(midi_sounding(), 0);
    CHECK_EQ(hub_holding(), 0);
    CHECK_EQ(tap.dups, 0);
}

/* The middle tile of nine loses power (pulled out with its cables). The
 * other eight go round it. It comes back with a key held down. */
static void tile_pulled_and_put_back(void)
{
    start(51);
    grid(3, 3, 0x0333);
    sim_run(&s, 4000);
    check_settled();

    midi_clear();
    tap_clear();
    int mid = pitch(4, 12), top = pitch(7, 10);
    CHECK(mid >= 0 && top >= 0 && mid != top);
    press(4, 12, 20);
    press(7, 10, 20);           /* (1, 2): its shortest way went through the middle */
    sim_run(&s, 200);
    CHECK_EQ(midi_sounding(), 2);

    sim_power(&s, 4, false);
    memset(tap.seen[4], 0, sizeof tap.seen[4]);         /* it will count its events from 0 again */
    play(1500, 0);
    check_settled();
    CHECK_EQ(H()->width, 3);
    CHECK_EQ(H()->height, 3);
    CHECK(HT(4) != NULL && HT(4)->silent);
    CHECK_EQ(midi_count(0x80, mid), 1);
    CHECK_EQ(midi_count(0x80, top), 0);
    CHECK_EQ(T(7)->node.parent_hops, 3);
    CHECK_EQ(midi_sounding(), 1);

    /* Back, the key still down. A tile that starts with a key down takes
     * that for the key's rest: no note, and none hangs. */
    sim_power(&s, 4, true);
    play(2000, 0);
    check_settled();
    CHECK_EQ(T(7)->node.parent_hops, 3);
    CHECK_EQ(midi_count(0x90, mid), 1);
    CHECK_EQ(midi_sounding(), 1);

    /* Let go, the tile learns where rest really is, and the key plays. */
    lift(4, 12, 20);
    sim_run(&s, 1500);
    press(4, 12, 20);
    sim_run(&s, 150);
    CHECK_EQ(midi_count(0x90, mid), 2);
    lift(4, 12, 20);
    lift(7, 10, 20);
    sim_run(&s, 200);
    CHECK_EQ(midi_sounding(), 0);
    CHECK_EQ(hub_holding(), 0);
    CHECK_EQ(tap.dups, 0);
    CHECK(tap_no_extra());
}

/* The hub loses power and gets it back; restarts on command; keeps what was
 * saved and nothing else. */
static void hub_restarts(void)
{
    start(61);
    grid(2, 2, 0x09);
    sim_run(&s, 3500);
    check_settled();

    press(0, 12, 20);
    sim_run(&s, 200);
    CHECK_EQ(hub_holding(), 1);

    /* off for a third of a second */
    sim_power(&s, SIM_HUB, false);
    sim_run(&s, 300);
    midi_clear();
    sim_power(&s, SIM_HUB, true);
    sim_run(&s, 3000);
    check_settled();
    CHECK_EQ(midi_count(0x90, pitch(0, 12)), 1);        /* the key that is still down */
    CHECK_EQ(midi_last(0x90, pitch(0, 12)), 64);
    CHECK_EQ(midi_sounding(), 1);
    CHECK_EQ(H()->stats.healed_on, 1);
    for (int i = 0; i < 4; i++)
        CHECK(T(i)->node.epoch >= 1);   /* every tile forgot the old hub first */

    /* off for a blink */
    sim_power(&s, SIM_HUB, false);
    sim_run(&s, 50);
    midi_clear();
    sim_power(&s, SIM_HUB, true);
    sim_run(&s, 3000);
    check_settled();
    CHECK_EQ(midi_count(0x90, pitch(0, 12)), 1);
    CHECK_EQ(midi_sounding(), 1);

    /* a setting that is saved lasts; one that is not, does not */
    SAY_OK("set base 60");
    SAY_OK("save hub");
    CHECK_EQ(s.hub.n.saves, 1);
    SAY_OK("set stepa 5");
    midi_clear();
    SAY_OK("reboot hub");
    sim_run(&s, 3500);
    CHECK_EQ(s.hub.n.reboots, 1);
    check_settled();
    SAY_OK("get base");
    CHECK(has_line("base = 60"));
    SAY_OK("get stepa");
    CHECK(has_line("stepa = 4"));
    CHECK_EQ(midi_count(0x90, pitch(0, 12)), 1);
    CHECK_EQ(pitch(0, 12), 60 + 8 + 7);

    /* commands work after all that: the tiles forgot the old hub's counters */
    SAY_OK("ping all");
    CHECK_EQ(lines_with("tile "), 4);
    lift(0, 12, 20);
    sim_run(&s, 200);
    CHECK_EQ(midi_sounding(), 0);
}

/* How hard a key is hit, and the presses that are not notes. */
static void speeds(void)
{
    start(71);
    grid(1, 1, 1);
    sim_run(&s, 3000);
    check_settled();
    int n = pitch(0, 22);

    /* all the way down in half a second: as soft as playing gets */
    midi_clear();
    tap_clear();
    press(0, 22, 500);
    sim_run(&s, 600);
    CHECK_EQ(tap.nnotes, 1);
    CHECK(tap.note[0].on);
    CHECK_EQ(tap.note[0].key, 22);
    CHECK_NEAR(tap.note[0].speed, 200, 20);
    CHECK_NEAR(midi_last(0x90, n), 19, 2);
    lift(0, 22, 500);
    sim_run(&s, 600);
    CHECK_EQ(tap.nnotes, 2);
    CHECK(!tap.note[1].on);
    CHECK_NEAR(tap.note[1].speed, 200, 20);
    CHECK_NEAR(midi_last(0x80, n), 19, 2);

    /* in 10 ms: hard */
    press(0, 22, 10);
    sim_run(&s, 100);
    CHECK_EQ(tap.nnotes, 3);
    CHECK_NEAR(tap.note[2].speed, 10000, 1000);
    CHECK_NEAR(midi_last(0x90, n), 122, 3);
    lift(0, 22, 10);
    sim_run(&s, 100);
    CHECK_EQ(tap.nnotes, 4);
    CHECK_NEAR(tap.note[3].speed, 10000, 1000);

    /* in 2 ms: as hard as it gets */
    press(0, 22, 2);
    sim_run(&s, 100);
    CHECK_EQ(tap.nnotes, 5);
    CHECK_EQ(midi_last(0x90, n), 127);
    CHECK_EQ(midi_count(0x90, n), 3);

    /* Half way up and down again: the note is struck again, off then on. */
    midi_clear();
    sim_move(&s, 0, 22, FLOOR * 3 / 10, 20);
    sim_run(&s, 20);
    press(0, 22, 20);
    sim_run(&s, 100);
    CHECK_EQ(tap.nnotes, 7);
    CHECK(!tap.note[5].on);
    CHECK(tap.note[6].on);
    CHECK_NEAR(tap.note[5].speed, 3500, 500);
    CHECK_NEAR(tap.note[6].speed, 3500, 500);
    CHECK_EQ(s.nmidi, 2);
    CHECK_EQ(s.midi[0][0] & 0xF0, 0x80);
    CHECK_EQ(s.midi[1][0] & 0xF0, 0x90);
    CHECK_EQ(midi_sounding(), -1);      /* (the log starts with the off: the note was on before) */

    /* A little way up and down again is the same note going on. */
    midi_clear();
    sim_move(&s, 0, 22, FLOOR * 55 / 100, 20);
    sim_run(&s, 60);
    press(0, 22, 20);
    sim_run(&s, 60);
    sim_move(&s, 0, 22, FLOOR * 45 / 100, 20);
    sim_run(&s, 60);
    press(0, 22, 20);
    sim_run(&s, 300);
    CHECK_EQ(tap.nnotes, 7);
    CHECK_EQ(s.nmidi, 0);
    lift(0, 22, 20);
    sim_run(&s, 100);
    CHECK_EQ(tap.nnotes, 8);
    CHECK_EQ(midi_count(0x80, n), 1);

    /* A key that goes less than far enough down is no note at all. */
    midi_clear();
    sim_move(&s, 0, 22, FLOOR * 4 / 10, 20);
    sim_run(&s, 100);
    lift(0, 22, 20);
    sim_run(&s, 300);
    CHECK_EQ(tap.nnotes, 8);
    CHECK_EQ(s.nmidi, 0);
    CHECK_EQ(T(0)->keys.key[22].state, KEY_IDLE);
    CHECK_EQ(hub_holding(), 0);
    CHECK_EQ(H()->stats.notes_lost, 0);
}

/* NOTE frames that get lost on the way: the tile's STATE puts things right
 * within a quarter of a second, and no note hangs. */
static void lost_notes_are_healed(void)
{
    start(81);
    grid(2, 1, 0x02);
    sim_run(&s, 3000);
    check_settled();
    int n = pitch(0, 9);

    /* the note on is lost */
    midi_clear();
    sim_drop(&s, hubcab, 1, FT_NOTE, 1);
    press(0, 9, 20);
    sim_run(&s, 60);
    CHECK_EQ(s.nmidi, 0);
    sim_run(&s, 200);
    CHECK_EQ(midi_count(0x90, n), 1);
    CHECK_EQ(midi_last(0x90, n), 64);
    CHECK_EQ(H()->stats.healed_on, 1);

    /* the note off is lost */
    sim_drop(&s, hubcab, 1, FT_NOTE, 1);
    lift(0, 9, 20);
    sim_run(&s, 60);
    CHECK_EQ(midi_count(0x80, n), 0);
    sim_run(&s, 200);
    CHECK_EQ(midi_count(0x80, n), 1);
    CHECK_EQ(H()->stats.healed_off, 1);
    CHECK_EQ(midi_sounding(), 0);

    /* the next note that arrives shows how many were missed */
    CHECK_EQ(H()->stats.notes_lost, 0);
    press(0, 9, 20);
    sim_run(&s, 100);
    CHECK_EQ(H()->stats.notes_lost, 2);
    CHECK_EQ(midi_count(0x90, n), 2);
    CHECK(midi_last(0x90, n) != 64);
    lift(0, 9, 20);
    sim_run(&s, 100);
    CHECK_EQ(midi_sounding(), 0);

    /* A short note lost whole: never heard, and nothing hangs. */
    midi_clear();
    sim_drop(&s, hubcab, 1, FT_NOTE, 2);
    press(0, 9, 10);
    sim_run(&s, 40);
    lift(0, 9, 10);
    sim_run(&s, 400);
    CHECK_EQ(s.nmidi, 0);
    CHECK_EQ(hub_holding(), 0);

    /* STATE frames lost: nobody notices. */
    sim_drop(&s, hubcab, 1, FT_STATE, 3);
    press(0, 9, 20);
    sim_run(&s, 400);
    lift(0, 9, 20);
    sim_run(&s, 200);
    CHECK_EQ(midi_count(0x90, n), 1);
    CHECK_EQ(midi_count(0x80, n), 1);
    CHECK_EQ(H()->stats.healed_on, 1);
    CHECK_EQ(H()->stats.healed_off, 1);
    CHECK_EQ(H()->stats.silent, 0);
    CHECK_EQ(H()->stats.notes_old, 0);
}

/* Commands: done once however often they have to be sent, answered, and
 * given up on when nobody is there. */
static void commands(void)
{
    start(91);
    grid(2, 1, 0x01);
    sim_run(&s, 3000);
    check_settled();
    uint32_t far = T(0)->node.id, near = T(1)->node.id;
    int far_ix = (int)(HT(0) - H()->tile);

    /* The answer is lost: the hub asks again, the tile answers again and
     * does not save twice. */
    sim_drop(&s, hubcab, 1, FT_REPLY, 1);
    SAY_OK("save %08x", (unsigned)far);
    CHECK_EQ(s.tile[0].n.saves, 1);
    CHECK_EQ(s.tile[1].n.saves, 0);

    /* The command is lost twice: the third copy does it. */
    tap_clear();
    sim_drop(&s, hubcab, 0, FT_CMD, 2);
    SAY_OK("tset %08x lo 200", (unsigned)far);
    CHECK_EQ(tap.cmd_parent[0], 1);
    CHECK_EQ(T(0)->keys.param[PARAM_LO], 200);
    CHECK_EQ(T(1)->keys.param[PARAM_LO], 150);
    SAY_OK("tget all lo");
    CHECK(has_linef("tile %d: lo = 200", far_ix));
    CHECK(has_linef("tile %d: lo = 150", (int)(HT(1) - H()->tile)));

    /* what a tile refuses, the console says */
    say("tset %08x lo 5", (unsigned)far);
    CHECK(has_linef("tile %d: value not allowed", far_ix));
    CHECK(has_line("error: 1 failed"));
    say("ping 7");
    CHECK(has_line("error: no such tile"));

    /* a ping goes two cables out and two back: half a millisecond on the wires */
    SAY_OK("ping %08x", (unsigned)far);
    CHECK_EQ(lines_with("tile "), 1);
    CHECK(has_linef("tile %d: 0 ms", far_ix) || has_linef("tile %d: 1 ms", far_ix) ||
          has_linef("tile %d: 2 ms", far_ix));

    /* One tile restarts, the other does not. */
    SAY_OK("reboot %08x", (unsigned)far);
    sim_run(&s, 300);
    CHECK_EQ(s.tile[0].n.reboots, 1);
    CHECK_EQ(s.tile[1].n.reboots, 0);
    sim_run(&s, 2500);
    check_settled();
    /* it started again from what `save` kept: that was before `tset` */
    CHECK_EQ(T(0)->keys.param[PARAM_LO], 150);

    /* All restart, once each, though the hub says it three times. */
    tap_clear();
    SAY_OK("reboot all");
    sim_run(&s, 400);
    CHECK_EQ(tap.cmd_hub, 3);
    CHECK_EQ(s.tile[0].n.reboots, 2);
    CHECK_EQ(s.tile[1].n.reboots, 1);
    sim_run(&s, 3000);
    check_settled();

    /* A tile that is gone: five tries, then the hub says so and goes on. */
    sim_power(&s, 0, false);
    tap_clear();
    say("ping all");
    CHECK(has_linef("tile %d: no answer", far_ix));
    CHECK(has_line("error: 1 failed"));
    CHECK_EQ(lines_with("tile "), 2);
    CHECK_EQ(tap.cmd_hub, 1 + CMD_TRIES);
    CHECK_EQ(H()->stats.no_answer, 1);
    (void)near;

    /* and when the hub has forgotten it, it is not asked at all */
    sim_run(&s, 3500);
    SAY_OK("ping all");
    CHECK_EQ(lines_with("tile "), 1);
}

/* Four tiles in a row all streaming their ADC values: far more than the
 * cable carries. What gives way is the stream; notes, STATE, INFO, replies
 * and commands all get through. */
static void full_queues_drop_the_stream(void)
{
    start(101);
    grid(4, 1, 0x05);
    sim_run(&s, 3500);
    check_settled();

    SAY_OK("tset all rawinterval 2");
    for (int i = 0; i < 4; i++) {
        SAY_OK("adc %08x on", (unsigned)T(i)->node.id);
        H()->stream_adc = 0;    /* the streams run; printing them would only fill the test's buffer */
    }
    sim_run(&s, 300);
    for (int i = 0; i < 4; i++)
        CHECK(HT(i)->flags & INFO_RAW);

    tap_clear();
    midi_clear();
    for (uint32_t t = 0; t < 2000; t++) {
        for (int i = 0; i < 4; i++) {
            uint32_t at = (t + 200u - 50u * (uint32_t)i) % 200u;
            if (at == 0)
                press(i, 0, 5);
            else if (at == 100)
                lift(i, 0, 5);
        }
        sim_run(&s, 1);
    }
    for (int i = 0; i < 4; i++)
        lift(i, 0, 5);
    sim_run(&s, 200);

    unsigned raw_made = 0, raw_in = 0;
    for (int i = 0; i < 4; i++) {
        CHECK_EQ(tap.up[i][UP(FT_NOTE)], 20);
        CHECK(tap.up[i][UP(FT_STATE)] >= 21);
        CHECK(tap.up[i][UP(FT_INFO)] >= 2);
        raw_made += tap.made[i][UP(FT_RAW)];
        raw_in += tap.up[i][UP(FT_RAW)];
    }
    CHECK_EQ(midi_count(0x90, -1), 40);
    CHECK_EQ(midi_count(0x80, -1), 40);
    CHECK_EQ(midi_sounding(), 0);
    CHECK_EQ(H()->stats.notes_lost, 0);
    CHECK_EQ(H()->stats.notes_old, 0);
    CHECK_EQ(H()->stats.healed_on + H()->stats.healed_off, 0);
    CHECK_EQ(tap.dups, 0);
    /* Each tile offers 1000 frames a second, 8800 in all in this time; a
     * cable carries under 2000 a second. The far tile's stream fits its
     * cable; from there on the tiles have to choose. */
    CHECK_NEAR(tap.made[0][UP(FT_RAW)], 2200, 30);
    CHECK_EQ(T(0)->node.queue_drops, 0);
    CHECK(raw_made < 6600);
    CHECK(raw_in > 2000);
    CHECK(raw_in < 4600);
    for (int i = 1; i < 4; i++)
        CHECK(T(i)->node.queue_drops > 100);

    /* commands and their answers */
    SAY_OK("ping all");
    CHECK_EQ(lines_with("tile "), 4);
    CHECK_EQ(H()->stats.no_answer, 0);
    SAY_OK("tiles");
    CHECK_EQ(lines_with("0 ") + lines_with("1 ") + lines_with("2 ") + lines_with("3 "), 4);

    SAY_OK("adc off");
    sim_run(&s, 200);
    tap_clear();
    sim_run(&s, 1000);
    for (int i = 0; i < 4; i++) {
        CHECK_EQ(tap.made[i][UP(FT_RAW)], 0);
        CHECK(!(HT(i)->flags & INFO_RAW));
    }
    check_settled();
}

/* Cables that lose three frames in a hundred and spoil three more, in a
 * square with a loop. Notes may go missing while it lasts; when it is over
 * nothing hangs and nothing was heard twice. */
static void noisy_cables(void)
{
    start(111);
    grid(2, 2, 0x0C);
    sim_run(&s, 3500);
    check_settled();

    for (int c = 0; c < s.ncables; c++) {
        for (int d = 0; d < 2; d++) {
            s.cable[c].loss[d] = 30;
            s.cable[c].corrupt[d] = 30;
        }
    }
    midi_clear();
    tap_clear();
    play(8000, 3);
    SAY_OK("tset all retrigger 150");   /* commands get through noise, by trying again */
    for (int i = 0; i < 4; i++)
        CHECK_EQ(T(i)->keys.param[PARAM_RETRIGGER], 150);

    for (int c = 0; c < s.ncables; c++) {
        for (int d = 0; d < 2; d++)
            s.cable[c].loss[d] = s.cable[c].corrupt[d] = 0;
    }
    sim_run(&s, 1500);
    check_settled();
    CHECK(midi_sounding() == 0);
    CHECK_EQ(hub_holding(), 0);
    CHECK_EQ(midi_count(0x90, -1), midi_count(0x80, -1));
    CHECK(midi_count(0x90, -1) >= 70);  /* of 80 presses, most were heard */
    CHECK_EQ(tap.dups, 0);
    CHECK(tap_no_extra());

    unsigned bad = H()->node.crc_drops, lost = 0;
    for (int i = 0; i < 4; i++) {
        bad += T(i)->node.crc_drops;
        CHECK_EQ(s.tile[i].n.rx_overruns, 0);
    }
    for (int c = 0; c < s.ncables; c++)
        lost += s.cable[c].lost[0] + s.cable[c].lost[1];
    CHECK(bad > 50);
    CHECK(lost > 50);
}

/* What flash keeps, and what happens when the writing goes wrong. */
static void flash(void)
{
    start(121);
    s.swing_spread = 20;        /* no two switches alike */
    grid(2, 1, 0x00);
    sim_run(&s, 3500);
    check_settled();
    uint32_t id = T(0)->node.id;
    int ix = (int)(HT(0) - H()->tile);
    CHECK(!(HT(0)->flags & INFO_CALIBRATED));

    /* Calibration: every key pressed to the floor once. */
    SAY_OK("cal start %08x", (unsigned)id);
    sim_run(&s, 50);
    CHECK(HT(0)->flags & INFO_CALIBRATING);
    midi_clear();
    for (int k = 0; k < KEYS_N; k++)
        press(0, k, 200);
    sim_run(&s, 300);
    for (int k = 0; k < KEYS_N; k++)
        lift(0, k, 200);
    sim_run(&s, 300);
    SAY_OK("cal stop %08x", (unsigned)id);
    CHECK(has_linef("tile %d: 40 of 40 keys measured", ix));
    for (int k = 0; k < KEYS_N; k++)
        CHECK_NEAR(T(0)->keys.key[k].swing / 16, s.tile[0].swing[k], 3);
    sim_run(&s, 50);
    CHECK(HT(0)->flags & INFO_CALIBRATED);
    CHECK(!(HT(0)->flags & INFO_CALIBRATING));
    CHECK_EQ(midi_sounding(), 0);

    /* saved, it is there after the power was off */
    SAY_OK("tset %08x lo 200", (unsigned)id);
    SAY_OK("save %08x", (unsigned)id);
    CHECK_EQ(s.tile[0].n.saves, 1);
    sim_power(&s, 0, false);
    sim_run(&s, 100);
    sim_power(&s, 0, true);
    sim_run(&s, 2500);
    check_settled();
    CHECK(HT(0)->flags & INFO_CALIBRATED);
    for (int k = 0; k < KEYS_N; k++)
        CHECK_NEAR(T(0)->keys.key[k].swing / 16, s.tile[0].swing[k], 3);
    SAY_OK("tget %08x lo", (unsigned)id);
    CHECK(has_linef("tile %d: lo = 200", ix));

    /* A write that fails: the tile says so. The old record is gone (the
     * sector was erased first), the tile carries on with what it has in
     * memory. */
    s.tile[0].n.flash_fails = true;
    say("save %08x", (unsigned)id);
    CHECK(has_linef("tile %d: failed", ix));
    CHECK(has_line("error: 1 failed"));
    s.tile[0].n.flash_fails = false;
    CHECK_EQ(T(0)->keys.param[PARAM_LO], 200);
    SAY_OK("save %08x", (unsigned)id);

    /* Power lost in the middle of a write: the tile starts with defaults. */
    SAY_OK("tset %08x lo 250", (unsigned)id);
    sim_type(&s, "save 0,0\r");
    for (int ms = 0; ms < 100 && !s.tile[0].n.saving; ms++)
        sim_run(&s, 1);
    CHECK(s.tile[0].n.saving);
    sim_power(&s, 0, false);
    sim_run(&s, 1000);
    sim_power(&s, 0, true);
    sim_run(&s, 2500);
    check_settled();
    CHECK_EQ(T(0)->keys.param[PARAM_LO], 150);
    CHECK_EQ(keys_measured(&T(0)->keys), 0);
    CHECK(!(HT(0)->flags & INFO_CALIBRATED));

    /* `save all`: every tile once, then the hub; `save`: the hub alone. */
    unsigned t0 = s.tile[0].n.saves, t1 = s.tile[1].n.saves;
    SAY_OK("save all");
    CHECK_EQ(s.tile[0].n.saves, t0 + 1);
    CHECK_EQ(s.tile[1].n.saves, t1 + 1);
    CHECK_EQ(s.hub.n.saves, 1);
    SAY_OK("save");
    CHECK_EQ(s.hub.n.saves, 2);
    CHECK_EQ(s.tile[0].n.saves, t0 + 1);

    s.hub.n.flash_fails = true;
    say("save hub");
    CHECK(has_line("hub: flash failed"));
    CHECK(has_line("error: 1 failed"));
    check_settled();
}

/* Putting new firmware on a tile through the hub: the tile goes into its
 * ROM, every other tile and the hub keep off the wires, the serial port
 * becomes a UART. Afterwards the keyboard is a keyboard again. */
static void bootloader_and_bridge(void)
{
    start(131);
    grid(3, 1, 0x03);
    sim_run(&s, 3500);
    check_settled();
    uint32_t id = T(0)->node.id;

    press(1, 5, 20);
    sim_run(&s, 200);
    CHECK_EQ(hub_holding(), 1);

    SAY_OK("bootloader %08x 2000", (unsigned)id);
    sim_run(&s, 60);
    unsigned long before = frames_sent();
    say("ping all");
    CHECK(has_line("error: the cable is quiet"));

    midi_clear();
    SAY_OK("bridge 115200");
    CHECK_EQ(s.hub.bridges, 1);
    CHECK(H()->bridging);
    CHECK_EQ(H()->bridge.baud, 115200);
    CHECK_EQ(H()->bridge.parity, 'e');
    CHECK_EQ(H()->bridge.swapped, s.hub.port[0].swapped);
    CHECK_EQ(hub_holding(), 0);         /* the notes were let go */
    CHECK_EQ(midi_count(0x80, -1), 1);

    sim_run(&s, 1700);
    CHECK_EQ(frames_sent(), before);    /* not a frame on any cable all that time */
    CHECK(s.tile[0].n.halted);
    CHECK_EQ(s.tile[0].n.bootloaders, 1);
    CHECK_EQ(s.tile[1].n.bootloaders, 0);
    CHECK_EQ(s.tile[2].n.bootloaders, 0);

    /* The tiles' quiet runs out while the bridge is still on: they talk
     * among themselves and the hub says nothing. */
    sim_run(&s, 1500);
    CHECK(frames_sent() > before);
    CHECK_EQ(s.cable[hubcab].sent[0], s.cable[hubcab].sent[0]);
    unsigned hub_sent = s.cable[hubcab].sent[0];
    CHECK_EQ(T(2)->node.parent, PORT_NONE);
    sim_type(&s, "tiles\r");            /* goes to the UART now, not the console */
    sim_run(&s, 100);
    CHECK_EQ(s.cable[hubcab].sent[0], hub_sent);
    CHECK(H()->bridging);

    /* The computer closes the port: back to normal. The tile in its ROM
     * stays there until its power goes off and on. */
    hub_bridge_end(H(), sim_now(&s, SIM_HUB));
    sim_run(&s, 4000);
    check_settled();
    CHECK(HT(0) == NULL);
    CHECK_EQ(H()->width, 2);
    CHECK_EQ(midi_count(0x90, pitch(1, 5)), 1);         /* the key that is still down */
    SAY_OK("tiles");
    CHECK(has_line("2 tiles"));

    sim_power(&s, 0, false);
    sim_power(&s, 0, true);
    sim_run(&s, 2500);
    check_settled();
    CHECK_EQ(H()->width, 3);

    /* the hub's own ROM */
    SAY_OK("bootloader hub");
    sim_run(&s, 300);
    CHECK(s.hub.n.halted);
    CHECK_EQ(s.hub.n.bootloaders, 1);
}

/* Sensors that differ from key to key and are never quite still. */
static void sensors_with_spread_and_noise(void)
{
    start(141);
    s.rest_spread = 150;
    s.swing_spread = 20;
    s.noise = 3;
    grid(2, 1, 0x02);
    sim_run(&s, 3000);
    check_settled();

    /* five seconds of nothing */
    midi_clear();
    tap_clear();
    sim_run(&s, 5000);
    CHECK_EQ(tap.nnotes, 0);
    CHECK_EQ(s.nmidi, 0);

    /* every key of both tiles, one after another */
    unsigned ons[2][KEYS_N] = { { 0 } }, offs[2][KEYS_N] = { { 0 } };
    for (int k = 0; k < KEYS_N; k++) {
        press(0, k, 15);
        press(1, KEYS_N - 1 - k, 40);
        sim_run(&s, 70);
        lift(0, k, 15);
        lift(1, KEYS_N - 1 - k, 40);
        sim_run(&s, 70);
    }
    sim_run(&s, 200);
    for (int i = 0; i < tap.nnotes; i++) {
        const seen_note_t *n = &tap.note[i];
        if (n->on)
            ons[n->tile][n->key]++;
        else
            offs[n->tile][n->key]++;
    }
    for (int k = 0; k < KEYS_N; k++) {
        CHECK_EQ(ons[0][k], 1);
        CHECK_EQ(offs[0][k], 1);
        CHECK_EQ(ons[1][k], 1);
        CHECK_EQ(offs[1][k], 1);
    }
    CHECK_EQ(tap.nnotes, 160);
    CHECK_EQ(midi_sounding(), 0);
    CHECK_EQ(hub_holding(), 0);
    CHECK_EQ(H()->stats.healed_on + H()->stats.healed_off, 0);

    /* How deep a held key is, as polyphonic aftertouch: off unless asked for. */
    int n = pitch(0, 17);
    midi_clear();
    press(0, 17, 20);
    sim_run(&s, 300);
    CHECK_EQ(midi_count(0xA0, -1), 0);
    lift(0, 17, 20);
    sim_run(&s, 100);

    SAY_OK("set aftertouch 1");
    SAY_OK("tset all posmode 1");
    SAY_OK("cal start");
    press(0, 17, 100);
    sim_run(&s, 200);
    lift(0, 17, 100);
    sim_run(&s, 200);
    SAY_OK("cal stop");         /* now the tile knows where this key's floor is */
    midi_clear();
    press(0, 17, 100);
    sim_run(&s, 300);
    CHECK(midi_count(0xA0, n) >= 3);
    CHECK_EQ(midi_count(0xA0, -1), midi_count(0xA0, n));
    CHECK(midi_last(0xA0, n) >= 125);   /* on the floor, give or take the noise */
    sim_move(&s, 0, 17, FLOOR * 8 / 10, 50);
    sim_run(&s, 200);
    CHECK_NEAR(midi_last(0xA0, n), 64, 12);     /* half way between `hi` and the floor */
    int count = midi_count(0xA0, n);
    lift(0, 17, 20);
    sim_run(&s, 300);
    CHECK_EQ(midi_count(0x80, n), 1);
    CHECK(midi_count(0xA0, n) <= count + 3);    /* a few on the way up, none after */
    CHECK_EQ(midi_sounding(), 0);
}

/* Two tiles cabled east to north: the hub cannot say where the second is.
 * It tells the user which cable is wrong and plays the tile it can place. */
static void wrong_cable(void)
{
    start(151);
    int a = sim_add_tile(&s), b = sim_add_tile(&s);
    place_u[a] = place_v[a] = place_u[b] = place_v[b] = 0;
    sim_cable(&s, b, PORT_W, a, PORT_N, false);
    hubcab = sim_cable(&s, SIM_HUB, 0, b, PORT_E, true);
    sim_run(&s, 3500);

    CHECK(HT(a) != NULL && !HT(a)->placed);
    CHECK(HT(b) != NULL && HT(b)->placed);
    CHECK_EQ(H()->nproblems, 1);
    CHECK_EQ(T(a)->node.parent, PORT_N);        /* the tree does not care which port it is */
    SAY_OK("grid");
    CHECK_EQ(lines_with("problem: tile "), 1);
    CHECK_EQ(lines_with("unplaced: "), 1);
    CHECK(strstr(s.text, "port W is cabled to") || strstr(s.text, "port N is cabled to"));

    /* its keys are followed, they just have no notes */
    midi_clear();
    SAY_OK("events on");
    s.ntext = 0;
    s.text[0] = 0;
    press(a, 4, 20);
    press(b, 4, 20);
    sim_run(&s, 200);
    CHECK_EQ(midi_count(0x90, -1), 1);
    CHECK_EQ(midi_count(0x90, 48 + 4 * 7), 1);
    CHECK_EQ(lines_with("ev "), 2);
    CHECK(strstr(s.text, " on ") != NULL);
    CHECK(strstr(s.text, " -1 ") != NULL);
    lift(a, 4, 20);
    lift(b, 4, 20);
    sim_run(&s, 200);
    CHECK_EQ(lines_with("ev "), 4);
    CHECK_EQ(midi_sounding(), 0);
}

/* Every node's millisecond clock wraps round to 0 in the middle of things:
 * nothing may notice. */
static void clocks_wrap(void)
{
    start(161);
    s.wrap_ms = 6000;
    sim_power(&s, SIM_HUB, false);      /* the hub started before wrap_ms was set: again */
    sim_power(&s, SIM_HUB, true);
    grid(2, 2, 0x05);
    sim_run(&s, 4000);
    check_settled();
    CHECK(sim_now(&s, SIM_HUB) > 0xFFFF0000u);

    midi_clear();
    tap_clear();
    int held = pitch(0, 12);
    press(0, 12, 20);
    play(4000, 0);                      /* 4 s in: the clocks pass 0 here */
    CHECK(sim_now(&s, SIM_HUB) < 10000u);
    CHECK(sim_now(&s, 0) < 10000u);
    check_settled();
    CHECK_EQ(midi_count(0x90, held), 1);
    CHECK_EQ(midi_count(0x80, held), 0);
    CHECK_EQ(midi_sounding(), 1);
    CHECK_EQ(midi_count(0x90, -1), 1 + 4 * 10);
    CHECK_EQ(H()->stats.silent, 0);
    CHECK_EQ(H()->stats.notes_lost, 0);
    CHECK_EQ(H()->stats.healed_on + H()->stats.healed_off, 0);
    for (int i = 0; i < 4; i++) {
        CHECK_EQ(T(i)->node.epoch, 0);          /* nobody lost the hub */
        CHECK(tap.up[i][UP(FT_STATE)] >= 42);
        CHECK(tap.up[i][UP(FT_INFO)] >= 4);
    }
    CHECK_EQ(tap.dups, 0);
    SAY_OK("ping all");
    CHECK_EQ(lines_with("tile "), 4);
    lift(0, 12, 20);
    sim_run(&s, 200);
    CHECK_EQ(midi_sounding(), 0);
}

int main(void)
{
    RUN(one_tile_crossed_cable);
    RUN(one_tile_straight_cable);
    RUN(row_with_mixed_cables);
    RUN(square_of_nine);
    RUN(cables_pulled_while_playing);
    RUN(hub_cable_pulled);
    RUN(column_pulled_off);
    RUN(tile_pulled_and_put_back);
    RUN(hub_restarts);
    RUN(speeds);
    RUN(lost_notes_are_healed);
    RUN(commands);
    RUN(full_queues_drop_the_stream);
    RUN(noisy_cables);
    RUN(flash);
    RUN(bootloader_and_bridge);
    RUN(sensors_with_spread_and_noise);
    RUN(wrong_cable);
    RUN(clocks_wrap);
    return T_DONE();
}
