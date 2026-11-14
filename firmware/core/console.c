/* The console: text lines in from the computer, text lines out.
 * README.md lists the commands. Every command answers with lines and then
 * `ok` or `error: ...`; the hub says nothing unasked, except for the two
 * streams (`events on`, `adc <tile> on`). */
#include "hub.h"

#include <string.h>

/* What a command's function returns: NULL when it went well, LATER when it
 * started a job that will print the last line itself, else what went wrong. */
static const char LATER[] = "later";

/* ---------------------------------------------------------------- reading --- */

/* The next word of the line, cut off with a 0; NULL at the end. */
static char *word(char **s)
{
    char *p = *s;

    while (*p == ' ')
        p++;
    if (!*p) {
        *s = p;
        return NULL;
    }
    char *w = p;
    while (*p && *p != ' ')
        p++;
    if (*p)
        *p++ = 0;
    *s = p;
    return w;
}

/* A decimal number, up to a thousand million either way. */
static bool number(const char *s, int32_t *out)
{
    bool neg = false;
    uint32_t v = 0;

    if (!s)
        return false;
    if (*s == '-') {
        neg = true;
        s++;
    }
    if (!*s)
        return false;
    for (; *s; s++) {
        if (*s < '0' || *s > '9' || v > 100000000u)
            return false;
        v = v * 10 + (uint32_t)(*s - '0');
    }
    *out = neg ? -(int32_t)v : (int32_t)v;
    return true;
}

static int hex_digit(char c)
{
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    if (c >= 'A' && c <= 'F')
        return c - 'A' + 10;
    return -1;
}

/* A tile, named by its number in the table, its place "u,v", or the eight
 * hex digits of its ID. */
static hub_tile_t *tile_ref(hub_t *h, char *s)
{
    char *comma = strchr(s, ',');
    int32_t a, b;

    if (strlen(s) == 8) {
        uint32_t id = 0;
        int i;
        for (i = 0; i < 8 && hex_digit(s[i]) >= 0; i++)
            id = id << 4 | (uint32_t)hex_digit(s[i]);
        if (i == 8)
            return hub_find(h, id);
    }
    if (comma) {
        *comma = 0;
        if (!number(s, &a) || !number(comma + 1, &b))
            return NULL;
        for (int i = 0; i < HUB_MAX_TILES; i++) {
            hub_tile_t *t = &h->tile[i];
            if (t->used && t->placed && t->u == a && t->v == b)
                return t;
        }
        return NULL;
    }
    if (number(s, &a) && a >= 0 && a < HUB_MAX_TILES && h->tile[a].used)
        return &h->tile[a];
    return NULL;
}

/* ---------------------------------------------------------------- showing --- */

static void end_line(hub_t *h)
{
    hub_say(h, "%s", "");
}

static char port_letter(uint8_t port)
{
    return port < TILE_PORTS ? "WNES"[port] : '-';
}

static int index_of(const hub_t *h, const hub_tile_t *t)
{
    return (int)(t - h->tile);
}

/* "tile 3", or its ID if the table does not have it. */
static void show_who(hub_t *h, uint32_t id)
{
    const hub_tile_t *t = hub_find(h, id);

    if (id == h->node.id)
        hub_print(h, "the hub");
    else if (t)
        hub_print(h, "tile %d", index_of(h, t));
    else
        hub_print(h, "%08x", (unsigned)id);
}

static void show_flags(hub_t *h, const hub_tile_t *t)
{
    if (t->flags & INFO_CALIBRATED)
        hub_print(h, " calibrated");
    if (t->flags & INFO_CALIBRATING)
        hub_print(h, " calibrating");
    if (t->flags & INFO_RAW)
        hub_print(h, " raw");
    if (t->silent)
        hub_print(h, " silent");
    if (t->cols != KEYS_COLS || t->rows != KEYS_ROWS)
        hub_print(h, " (%u x %u keys: not supported)", t->cols, t->rows);
}

static const char *cmd_help(hub_t *h, char **args)
{
    static const char *const text[] = {
        "tiles | grid | info <tile>      what is plugged in, and where",
        "get [name] | set <name> <n>     hub settings: base stepa stepb channel vel1 vel127",
        "                                bend aftertouch pd",
        "layout [harmonic|janko|fifths|custom <a> <b>]",
        "tget <tile|all> <name> | tset <tile|all> <name> <n>",
        "                                tile parameters: lo hi release retrigger posmode",
        "                                posinterval rawinterval swing",
        "cal start|stop|forget [tile|all]  measure how far the keys go down",
        "save [hub|all|<tile>]           keep settings and calibration in flash",
        "ping [tile|all] | stats | version",
        "events on|off                   print every key event and tile log line",
        "adc <tile> on|off | adc off     print one tile's ADC values",
        "power [auto|off|retry]",
        "panic                           all notes off",
        "quiet <ms>                      the tiles stop sending for a while",
        "reboot hub|all|<tile>",
        "bootloader hub|<tile> [quiet ms]",
        "bridge <baud> [n|e|o] [straight|swapped]",
        "                                the serial port becomes a UART on the cable",
        "a tile is its number in `tiles`, its place `u,v`, or its 8-digit ID",
    };

    (void)args;
    for (size_t i = 0; i < sizeof text / sizeof text[0]; i++)
        hub_say(h, "%s", text[i]);
    return NULL;
}

static const char *cmd_version(hub_t *h, char **args)
{
    (void)args;
    hub_say(h, "piano hub, firmware %04x, protocol %d, id %08x", FW_VERSION, PROTO_VERSION,
            (unsigned)h->node.id);
    return NULL;
}

static const char *cmd_tiles(hub_t *h, char **args)
{
    int n = 0;

    (void)args;
    for (int i = 0; i < HUB_MAX_TILES; i++) {
        const hub_tile_t *t = &h->tile[i];
        if (!t->used)
            continue;
        n++;
        hub_print(h, "%d %08x ", i, (unsigned)t->id);
        if (t->placed)
            hub_print(h, "at %d,%d", t->u, t->v);
        else
            hub_print(h, "unplaced");
        if (t->have_info) {
            hub_print(h, " parent %c", port_letter(t->parent));
            if (t->hops != 0xFF)
                hub_print(h, " hops %u", t->hops);
            hub_print(h, " fw %04x", t->version);
            show_flags(h, t);
        } else {
            hub_print(h, " (no info yet)");
        }
        end_line(h);
    }
    hub_say(h, "%d tile%s", n, n == 1 ? "" : "s");
    return NULL;
}

static const char *cmd_grid(hub_t *h, char **args)
{
    const hub_tile_t *root = hub_find(h, hub_root(h));
    bool any = false;

    (void)args;
    if (root)
        hub_say(h, "hub on tile %d", index_of(h, root));
    else
        hub_say(h, "hub: no tile on its cable");
    hub_say(h, "%u x %u", h->width, h->height);

    /* the top row first, as the keyboard lies on the table */
    for (int v = h->height - 1; v >= 0; v--) {
        for (int u = 0; u < h->width; u++) {
            int at = -1;
            for (int i = 0; i < HUB_MAX_TILES; i++) {
                const hub_tile_t *t = &h->tile[i];
                if (t->used && t->placed && t->u == u && t->v == v)
                    at = i;
            }
            if (at >= 0)
                hub_print(h, "%3d", at);
            else
                hub_print(h, "  .");
        }
        end_line(h);
    }

    for (int i = 0; i < HUB_MAX_TILES; i++) {
        if (h->tile[i].used && !h->tile[i].placed) {
            hub_print(h, any ? " %d" : "unplaced: %d", i);
            any = true;
        }
    }
    if (any)
        end_line(h);

    for (uint8_t i = 0; i < h->nproblems; i++) {
        const hub_problem_t *p = &h->problem[i];
        hub_print(h, "problem: ");
        show_who(h, p->a);
        hub_print(h, " port %c is cabled to ", port_letter(p->a_port));
        show_who(h, p->b);
        hub_print(h, " port %c", port_letter(p->b_port));
        if (p->what == PROBLEM_PLACE)
            hub_print(h, ", which the other cables contradict");
        end_line(h);
    }
    return NULL;
}

static const char *cmd_info(hub_t *h, char **args)
{
    char *who = word(args);
    const hub_tile_t *t = who ? tile_ref(h, who) : NULL;
    unsigned down = 0;

    if (!who)
        return "usage: info <tile>";
    if (!t)
        return "no such tile";

    hub_print(h, "tile %d id %08x ", index_of(h, t), (unsigned)t->id);
    if (t->placed)
        hub_say(h, "at %d,%d", t->u, t->v);
    else
        hub_say(h, "unplaced");
    for (uint8_t k = 0; k < KEYS_N; k++)
        down += KEY_BIT(t->on, k);
    hub_say(h, "heard %u ms ago, %u keys down", (unsigned)since(h->now, t->last_heard), down);
    if (!t->have_info) {
        hub_say(h, "no info yet");
        return NULL;
    }
    hub_print(h, "firmware %04x, %u x %u keys,%s", t->version, t->cols, t->rows,
              t->flags & INFO_CALIBRATED ? "" : " not calibrated");
    show_flags(h, t);
    end_line(h);
    if (t->parent == PORT_NONE)
        hub_say(h, "no way to the hub");
    else
        hub_say(h, "to the hub: port %c, %u tiles between", port_letter(t->parent), t->hops);
    for (uint8_t p = 0; p < TILE_PORTS; p++) {
        hub_print(h, "%c: ", port_letter(p));
        if (t->nbr_id[p] == ID_NONE) {
            hub_say(h, "nobody");
            continue;
        }
        show_who(h, t->nbr_id[p]);
        if (t->nbr_id[p] == h->node.id)
            end_line(h);
        else
            hub_say(h, " port %c", port_letter(t->nbr_port[p]));
    }
    hub_say(h, "up %u s, %u bad frames, %u frames dropped from a full queue", t->uptime,
            t->crc_drops, t->queue_drops);
    return NULL;
}

/* ----------------------------------------------------------- hub settings --- */

enum { S_BASE, S_STEPA, S_STEPB, S_CHANNEL, S_VEL1, S_VEL127, S_BEND, S_AFTERTOUCH, S_PD, S_COUNT };

static const struct {
    const char *name;
    int32_t lo, hi;
} setting[S_COUNT] = {
    [S_BASE] = { "base", -500, 500 },
    [S_STEPA] = { "stepa", -24, 24 },
    [S_STEPB] = { "stepb", -24, 24 },
    [S_CHANNEL] = { "channel", 1, 16 },         /* as musicians count them */
    [S_VEL1] = { "vel1", 1, 65535 },
    [S_VEL127] = { "vel127", 1, 65535 },
    [S_BEND] = { "bend", -100, 100 },
    [S_AFTERTOUCH] = { "aftertouch", 0, 1 },
    [S_PD] = { "pd", 5, 15 },
};

static int32_t setting_get(const hub_settings_t *s, int which)
{
    switch (which) {
    case S_BASE:        return s->base;
    case S_STEPA:       return s->step_a;
    case S_STEPB:       return s->step_b;
    case S_CHANNEL:     return s->channel + 1;
    case S_VEL1:        return s->vel1;
    case S_VEL127:      return s->vel127;
    case S_BEND:        return s->bend;
    case S_AFTERTOUCH:  return s->aftertouch;
    default:            return s->pd_volts;
    }
}

static void setting_put(hub_settings_t *s, int which, int32_t x)
{
    switch (which) {
    case S_BASE:        s->base = (int16_t)x; break;
    case S_STEPA:       s->step_a = (int8_t)x; break;
    case S_STEPB:       s->step_b = (int8_t)x; break;
    case S_CHANNEL:     s->channel = (uint8_t)(x - 1); break;
    case S_VEL1:        s->vel1 = (uint16_t)x; break;
    case S_VEL127:      s->vel127 = (uint16_t)x; break;
    case S_BEND:        s->bend = (int8_t)x; break;
    case S_AFTERTOUCH:  s->aftertouch = (uint8_t)x; break;
    default:            s->pd_volts = (uint8_t)x; break;
    }
}

static int setting_named(const char *name)
{
    for (int i = 0; i < S_COUNT; i++) {
        if (!strcmp(name, setting[i].name))
            return i;
    }
    return -1;
}

static const char *cmd_get(hub_t *h, char **args)
{
    const char *name = word(args);
    int which = name ? setting_named(name) : -1;

    if (name && which < 0)
        return "no such setting";
    for (int i = 0; i < S_COUNT; i++) {
        if (!name || i == which)
            hub_say(h, "%s = %d", setting[i].name, (int)setting_get(&h->set, i));
    }
    return NULL;
}

static const char *cmd_set(hub_t *h, char **args)
{
    const char *name = word(args), *value = word(args);
    hub_settings_t s = h->set;
    int32_t x;

    if (!name || !number(value, &x))
        return "usage: set <name> <number>";
    int which = setting_named(name);
    if (which < 0)
        return "no such setting";
    if (x < setting[which].lo || x > setting[which].hi)
        return "out of range";
    setting_put(&s, which, x);
    if (!settings_ok(&s))
        return which == S_PD ? "pd is 5, 9, 12 or 15" : "vel127 has to be above vel1";

    if (which == S_CHANNEL)
        hub_release_all(h);             /* their note offs belong on the old channel */
    h->set = s;
    if (which == S_PD)
        power_set_pd(&h->power, s.pd_volts, h->now);
    return NULL;
}

static const struct {
    const char *name;
    int8_t a, b;
} layout[] = {
    { "harmonic", 4, 7 },
    { "janko", 1, 2 },
    { "fifths", 7, 12 },
};
#define LAYOUTS  (sizeof layout / sizeof layout[0])

static const char *cmd_layout(hub_t *h, char **args)
{
    const char *name = word(args);
    int32_t a, b;

    if (!name) {
        const char *is = "custom";
        for (size_t i = 0; i < LAYOUTS; i++) {
            if (h->set.step_a == layout[i].a && h->set.step_b == layout[i].b)
                is = layout[i].name;
        }
        hub_say(h, "%s %d %d", is, h->set.step_a, h->set.step_b);
        return NULL;
    }
    if (!strcmp(name, "custom")) {
        const char *sa = word(args), *sb = word(args);
        if (!number(sa, &a) || !number(sb, &b))
            return "usage: layout custom <a> <b>";
        if (a < -24 || a > 24 || b < -24 || b > 24)
            return "out of range";
        h->set.step_a = (int8_t)a;
        h->set.step_b = (int8_t)b;
        return NULL;
    }
    for (size_t i = 0; i < LAYOUTS; i++) {
        if (!strcmp(name, layout[i].name)) {
            h->set.step_a = layout[i].a;
            h->set.step_b = layout[i].b;
            return NULL;
        }
    }
    return "no such layout";
}

/* ---------------------------------------------------------- tile commands --- */

static const char *const param_name[TILE_PARAMS + 1] = {
    [PARAM_LO] = "lo",
    [PARAM_HI] = "hi",
    [PARAM_RELEASE] = "release",
    [PARAM_RETRIGGER] = "retrigger",
    [PARAM_POS_MODE] = "posmode",
    [PARAM_POS_INTERVAL] = "posinterval",
    [PARAM_RAW_INTERVAL] = "rawinterval",
    [PARAM_SWING] = "swing",
};

const char *console_param_name(uint8_t param)
{
    return param >= 1 && param <= TILE_PARAMS ? param_name[param] : "?";
}

static uint8_t param_named(const char *name)
{
    for (uint8_t i = 1; i <= TILE_PARAMS; i++) {
        if (!strcmp(name, param_name[i]))
            return i;
    }
    return 0;
}

const char *console_status_name(uint8_t status)
{
    switch (status) {
    case ST_OK:         return "ok";
    case ST_UNKNOWN:    return "does not know that";
    case ST_BAD_ARG:    return "value not allowed";
    case ST_FAILED:     return "failed";
    case ST_BUSY:       return "busy";
    default:            return "odd reply";
    }
}

/* Start a job for the tile `who` names, or for all of them. */
static const char *job(hub_t *h, char *who, uint8_t kind, uint8_t op, const uint8_t *arg,
                       uint8_t arg_len)
{
    hub_job_t *j = &h->job;

    if (h->node.quiet != QUIET_OFF)
        return "the cable is quiet";
    memset(j, 0, sizeof *j);
    if (!strcmp(who, "all")) {
        for (int i = 0; i < HUB_MAX_TILES; i++) {
            if (h->tile[i].used)
                j->target[j->ntargets++] = h->tile[i].id;
        }
        if (j->ntargets == 0)
            return "no tiles";
    } else {
        const hub_tile_t *t = tile_ref(h, who);
        if (!t)
            return "no such tile";
        j->target[0] = t->id;
        j->ntargets = 1;
    }
    hub_job_start(h, kind, op, arg, arg_len);
    return LATER;
}

void console_reply(hub_t *h, const hub_tile_t *t, uint8_t status, const uint8_t *data, uint8_t n)
{
    int ix = index_of(h, t);

    if (status != ST_OK) {
        hub_say(h, "tile %d: %s", ix, console_status_name(status));
        return;
    }
    switch (h->job.kind) {
    case JOB_PING:
        hub_say(h, "tile %d: %u ms", ix, (unsigned)h->job.took);
        break;
    case JOB_GET:
        if (n >= 4)
            hub_say(h, "tile %d: %s = %d", ix, console_param_name(h->job.param),
                    (int)(int32_t)get32(data));
        break;
    case JOB_CAL_STOP:
        if (n >= 1)
            hub_say(h, "tile %d: %u of %d keys measured", ix, data[0], KEYS_N);
        break;
    default:
        break;
    }
}

static const char *cmd_tget(hub_t *h, char **args)
{
    char *who = word(args);
    const char *name = word(args);
    uint8_t param = name ? param_named(name) : 0;

    if (!who || !name)
        return "usage: tget <tile|all> <name>";
    if (!param)
        return "no such parameter";
    const char *e = job(h, who, JOB_GET, OP_GET, &param, 1);
    h->job.param = param;
    return e;
}

static const char *cmd_tset(hub_t *h, char **args)
{
    char *who = word(args);
    const char *name = word(args), *value = word(args);
    uint8_t arg[5];
    int32_t x;

    if (!who || !name || !number(value, &x))
        return "usage: tset <tile|all> <name> <number>";
    arg[0] = param_named(name);
    if (!arg[0])
        return "no such parameter";
    put32(arg + 1, (uint32_t)x);
    return job(h, who, JOB_PLAIN, OP_SET, arg, sizeof arg);
}

static char all[] = "all";      /* job() may write into the name it is given */

static const char *cmd_cal(hub_t *h, char **args)
{
    const char *what = word(args);
    char *who = word(args);
    uint8_t arg;

    if (what && !strcmp(what, "start"))
        arg = CAL_START;
    else if (what && !strcmp(what, "stop"))
        arg = CAL_STOP;
    else if (what && !strcmp(what, "forget"))
        arg = CAL_FORGET;
    else
        return "usage: cal start|stop|forget [tile|all]";
    return job(h, who ? who : all, arg == CAL_STOP ? JOB_CAL_STOP : JOB_PLAIN, OP_CAL, &arg, 1);
}

static const char *cmd_ping(hub_t *h, char **args)
{
    char *who = word(args);

    return job(h, who ? who : all, JOB_PING, OP_PING, NULL, 0);
}

static const char *cmd_save(hub_t *h, char **args)
{
    char *who = word(args);
    bool tiles = false;

    if (who && strcmp(who, "hub") && strcmp(who, "all"))
        return job(h, who, JOB_PLAIN, OP_SAVE, NULL, 0);

    /* `all`: the tiles first, if there are any; then the hub itself */
    for (int i = 0; i < HUB_MAX_TILES; i++) {
        if (who && !strcmp(who, "all") && h->tile[i].used)
            tiles = true;
    }
    if (tiles) {
        const char *e = job(h, who, JOB_PLAIN, OP_SAVE, NULL, 0);
        if (e != LATER)
            return e;
    } else {
        memset(&h->job, 0, sizeof h->job);
        hub_job_start(h, JOB_PLAIN, OP_SAVE, NULL, 0);
    }
    h->job.then_flash = true;
    return LATER;
}

static const char *cmd_events(hub_t *h, char **args)
{
    const char *what = word(args);

    if (what && !strcmp(what, "on"))
        h->stream_events = true;
    else if (what && !strcmp(what, "off"))
        h->stream_events = false;
    else
        return "usage: events on|off";
    return NULL;
}

static const char *cmd_adc(hub_t *h, char **args)
{
    char *who = word(args);
    const char *what = word(args);
    uint8_t on;

    if (who && !what && !strcmp(who, "off")) {
        /* whoever is streaming, known to the hub or not */
        if (h->node.quiet != QUIET_OFF)
            return "the cable is quiet";
        on = 0;
        h->stream_adc = 0;
        hub_broadcast(h, OP_RAW, &on, 1);
        return NULL;
    }
    if (who && what && !strcmp(what, "on"))
        on = 1;
    else if (who && what && !strcmp(what, "off"))
        on = 0;
    else
        return "usage: adc <tile> on|off, or adc off";

    const hub_tile_t *t = tile_ref(h, who);
    if (!t)
        return "no such tile";
    uint32_t id = t->id;
    const char *e = job(h, who, JOB_PLAIN, OP_RAW, &on, 1);
    if (e == LATER) {
        if (on)
            h->stream_adc = id;
        else if (h->stream_adc == id)
            h->stream_adc = 0;
    }
    return e;
}

static const char *cmd_quiet(hub_t *h, char **args)
{
    uint8_t arg[2];
    int32_t ms;

    if (!number(word(args), &ms) || ms < 1 || ms > 65535)
        return "usage: quiet <ms>, 1 to 65535";
    if (h->node.quiet != QUIET_OFF)
        return "the cable is quiet";
    put16(arg, (uint16_t)ms);
    hub_broadcast(h, OP_QUIET, arg, sizeof arg);
    node_quiet(&h->node, (uint32_t)ms, h->now);
    return NULL;
}

static const char *cmd_reboot(hub_t *h, char **args)
{
    char *who = word(args);

    if (!who)
        return "usage: reboot hub|all|<tile>";
    if (!strcmp(who, "hub")) {
        h->later = HUB_ACT_REBOOT;
        h->later_at = h->now + ACTION_DELAY_MS;
        return NULL;
    }
    if (!strcmp(who, "all")) {
        if (h->node.quiet != QUIET_OFF)
            return "the cable is quiet";
        hub_broadcast(h, OP_REBOOT, NULL, 0);
        return NULL;
    }
    return job(h, who, JOB_PLAIN, OP_REBOOT, NULL, 0);
}

static const char *cmd_bootloader(hub_t *h, char **args)
{
    char *who = word(args);
    const char *time = word(args);
    int32_t ms = HUB_QUIET_MS;

    if (!who || !strcmp(who, "all"))
        return "usage: bootloader hub|<tile> [quiet ms]";
    if (!strcmp(who, "hub")) {
        h->later = HUB_ACT_BOOTLOADER;
        h->later_at = h->now + ACTION_DELAY_MS;
        return NULL;
    }
    if (time && (!number(time, &ms) || ms < 1 || ms > 65535))
        return "quiet ms: 1 to 65535";
    /* The tile restarts into its ROM; then everybody keeps off the wires,
     * so the ROM hears only the computer (`bridge`). */
    const char *e = job(h, who, JOB_PLAIN, OP_BOOTLOADER, NULL, 0);
    if (e == LATER) {
        h->job.then_quiet = true;
        h->job.quiet_ms = (uint16_t)ms;
    }
    return e;
}

static const char *cmd_bridge(hub_t *h, char **args)
{
    const char *opt;
    int32_t baud;
    hub_bridge_t b = { 0, 'e', hub_swapped(h) };       /* the ROM bootloader wants even parity */

    if (!number(word(args), &baud) || baud < 1200 || baud > 1000000)
        return "usage: bridge <baud> [n|e|o] [straight|swapped]";
    b.baud = (uint32_t)baud;
    while ((opt = word(args)) != NULL) {
        if (!strcmp(opt, "n") || !strcmp(opt, "e") || !strcmp(opt, "o"))
            b.parity = opt[0];
        else if (!strcmp(opt, "straight"))
            b.swapped = false;
        else if (!strcmp(opt, "swapped"))
            b.swapped = true;
        else
            return "usage: bridge <baud> [n|e|o] [straight|swapped]";
    }
    /* From here the hub says nothing on the cable and the tiles' notes are
     * nobody's: let them go now. The port switches the serial port over
     * once the `ok` below has left. */
    hub_release_all(h);
    node_pause(&h->node, h->now);
    h->bridge = b;
    h->bridging = true;
    h->actions |= HUB_ACT_BRIDGE;
    return NULL;
}

/* ------------------------------------------------------------------ power --- */

static const char *cmd_power(hub_t *h, char **args)
{
    const char *what = word(args);
    const power_t *p = &h->power;
    const power_in_t *in = &p->in;

    if (what) {
        if (!strcmp(what, "auto"))
            power_command(&h->power, PWR_CMD_AUTO, h->now);
        else if (!strcmp(what, "off"))
            power_command(&h->power, PWR_CMD_OFF, h->now);
        else if (!strcmp(what, "retry"))
            power_command(&h->power, PWR_CMD_RETRY, h->now);
        else
            return "usage: power [auto|off|retry]";
        return NULL;
    }

    hub_say(h, "state: %s", power_state_name(p));
    hub_say(h, "tiles: %u mV", in->vin_mv);
    hub_say(h, "charger: %u mV, %u mA, asked for %u V%s", in->vpd_mv, in->ipd_ma, p->pd_ask,
            in->pd_good ? ", agreed" : "");
    if (h->pd.addr)
        hub_say(h, "pd chip: at 0x%02x, status 0x%02x, charger offers %u mA, %u errors",
                h->pd.addr, h->pd.status, h->pd.max_ma, h->pd.errors);
    else
        hub_say(h, "pd chip: not found (it is powered by the charger)");
    hub_say(h, "computer: %u mV, %u mA, offers %u mA (cc %u and %u mV), limit %s", in->vhost_mv,
            in->ihost_ma, power_host_offer_ma(p), in->cc1_mv, in->cc2_mv,
            p->ilim_hi ? "1.43 A" : "0.5 A");
    hub_say(h, "usb: %s%s", in->usb_configured ? "configured" : "not configured",
            in->usb_suspended ? ", suspended" : "");
    if (p->fault != PWR_OK)
        hub_say(h, "last fault: %s on the %s path, %u in a row",
                p->fault == PWR_FAULT_FLAG ? "eFuse flag" : "output low",
                p->fault_path == PWR_PD ? "charger" : "computer", p->tries);
    return NULL;
}

static const char *cmd_stats(hub_t *h, char **args)
{
    const hub_stats_t *s = &h->stats;

    (void)args;
    hub_say(h, "notes: %u late, %u lost, %u started and %u stopped by STATE",
            (unsigned)s->notes_old, (unsigned)s->notes_lost, (unsigned)s->healed_on,
            (unsigned)s->healed_off);
    hub_say(h, "tiles: %u times silent, %u frames from more tiles than fit",
            (unsigned)s->silent, (unsigned)s->no_room);
    hub_say(h, "commands: %u unanswered", (unsigned)s->no_answer);
    hub_say(h, "usb: %u midi messages and %u characters dropped", (unsigned)s->midi_lost,
            (unsigned)s->out_lost);
    hub_say(h, "cable: %u bad frames, %u dropped from a full queue", h->node.crc_drops,
            h->node.queue_drops);
    return NULL;
}

static const char *cmd_panic(hub_t *h, char **args)
{
    (void)args;
    hub_panic(h);
    return NULL;
}

/* ------------------------------------------------------------- the console --- */

static const struct {
    const char *name;
    const char *(*run)(hub_t *h, char **args);
} command[] = {
    { "help", cmd_help },
    { "version", cmd_version },
    { "tiles", cmd_tiles },
    { "grid", cmd_grid },
    { "info", cmd_info },
    { "get", cmd_get },
    { "set", cmd_set },
    { "layout", cmd_layout },
    { "tget", cmd_tget },
    { "tset", cmd_tset },
    { "cal", cmd_cal },
    { "save", cmd_save },
    { "ping", cmd_ping },
    { "events", cmd_events },
    { "adc", cmd_adc },
    { "power", cmd_power },
    { "stats", cmd_stats },
    { "panic", cmd_panic },
    { "quiet", cmd_quiet },
    { "reboot", cmd_reboot },
    { "bootloader", cmd_bootloader },
    { "bridge", cmd_bridge },
};

void console_line(hub_t *h, char *line)
{
    char *rest = line;
    const char *name = word(&rest);

    if (!name)
        return;
    for (size_t i = 0; i < sizeof command / sizeof command[0]; i++) {
        if (strcmp(name, command[i].name))
            continue;
        const char *e = command[i].run(h, &rest);
        if (!e)
            hub_say(h, "ok");
        else if (e != LATER)
            hub_say(h, "error: %s", e);
        return;
    }
    hub_say(h, "error: no such command (try help)");
}

size_t hub_console_rx(hub_t *h, const uint8_t *data, size_t len, uint32_t now)
{
    size_t i = 0;

    h->now = now;
    if (h->bridging)
        return len;
    /* One command at a time: while one is still talking to tiles, the rest
     * of what was typed waits with the caller. */
    while (i < len && h->job.kind == JOB_NONE && h->bcast_left == 0) {
        if (h->bridging)
            return len;                 /* `bridge` was the last command */
        char c = (char)data[i++];
        if (c == '\r' || c == '\n') {
            if (h->line_over) {
                hub_say(h, "error: line too long");
            } else if (h->line_len) {
                h->line[h->line_len] = 0;
                console_line(h, h->line);
            }
            h->line_len = 0;
            h->line_over = false;
        } else if (c == 8 || c == 127) {
            if (h->line_len)
                h->line_len--;
        } else if (c >= 0x20 && c < 0x7F) {
            if (h->line_len < HUB_LINE_MAX)
                h->line[h->line_len++] = c;
            else
                h->line_over = true;
        }
    }
    return i;
}
