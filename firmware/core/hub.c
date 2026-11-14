#include "hub.h"

#include <string.h>

#define NO_NOTE        0xFFu
#define FLASH_WAIT_MS  2000u    /* how long the port gets to write the settings */

/* ------------------------------------------------------------ console out --- */

static void out_put(hub_t *h, const char *s, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        if (h->out_count == HUB_OUT_LEN) {
            h->stats.out_lost += (uint32_t)(n - i);
            return;
        }
        h->out[(h->out_head + h->out_count) % HUB_OUT_LEN] = s[i];
        h->out_count++;
    }
}

void hub_print(hub_t *h, const char *f, ...)
{
    char buf[200];
    va_list ap;

    va_start(ap, f);
    size_t n = vfmt(buf, sizeof buf, f, ap);
    va_end(ap);
    out_put(h, buf, n);
}

void hub_say(hub_t *h, const char *f, ...)
{
    char buf[200];
    va_list ap;

    va_start(ap, f);
    size_t n = vfmt(buf, sizeof buf, f, ap);
    va_end(ap);
    out_put(h, buf, n);
    out_put(h, "\r\n", 2);
}

size_t hub_console_read(hub_t *h, uint8_t *buf, size_t max)
{
    size_t n = 0;

    while (n < max && h->out_count) {
        buf[n++] = (uint8_t)h->out[h->out_head];
        h->out_head = (uint16_t)((h->out_head + 1) % HUB_OUT_LEN);
        h->out_count--;
    }
    return n;
}

/* ------------------------------------------------------------------- MIDI --- */

static void midi_put(hub_t *h, uint8_t status, uint8_t d1, uint8_t d2)
{
    if (h->midi_count == HUB_MIDI_LEN) {
        h->stats.midi_lost++;
        return;
    }
    uint8_t *m = h->midi[(h->midi_head + h->midi_count) % HUB_MIDI_LEN];
    m[0] = (uint8_t)(status | h->set.channel);
    m[1] = d1;
    m[2] = d2;
    h->midi_count++;
}

bool hub_midi_get(hub_t *h, uint8_t msg[3])
{
    if (h->midi_count == 0)
        return false;
    memcpy(msg, h->midi[h->midi_head], 3);
    h->midi_head = (uint16_t)((h->midi_head + 1) % HUB_MIDI_LEN);
    h->midi_count--;
    return true;
}

/* Two keys can have the same note. The note sounds while either is down: a
 * second key strikes it again, and it stops when the last one lets go. */
static void note_start(hub_t *h, uint8_t note, uint8_t vel)
{
    if (h->held[note])
        midi_put(h, 0x80, note, 64);
    if (h->held[note] < 0xFF)
        h->held[note]++;
    midi_put(h, 0x90, note, vel);
}

static void note_stop(hub_t *h, uint8_t note, uint8_t vel)
{
    if (h->held[note] && --h->held[note] == 0)
        midi_put(h, 0x80, note, vel);
}

/* ------------------------------------------------------------------- keys --- */

static int tile_index(const hub_t *h, const hub_tile_t *t)
{
    return (int)(t - h->tile);
}

static void key_off(hub_t *h, hub_tile_t *t, uint8_t key, uint8_t vel, uint16_t speed)
{
    if (!KEY_BIT(t->on, key))
        return;
    t->on[key >> 3] &= (uint8_t)~(1u << (key & 7));
    /* the note the key started, whatever the settings say by now */
    if (t->note[key] != NO_NOTE)
        note_stop(h, t->note[key], vel);
    if (h->stream_events)
        hub_say(h, "ev %d %u off %u %d", tile_index(h, t), key, speed,
                t->note[key] == NO_NOTE ? -1 : t->note[key]);
    t->note[key] = NO_NOTE;
}

static void key_on(hub_t *h, hub_tile_t *t, uint8_t key, uint8_t vel, uint16_t speed)
{
    key_off(h, t, key, 64, 0);          /* if it was on, its note off was lost */

    int note = t->placed ? notes_note(&h->set, t->u, t->v, key) : -1;
    t->on[key >> 3] |= (uint8_t)(1u << (key & 7));
    t->note[key] = note < 0 ? NO_NOTE : (uint8_t)note;
    if (note >= 0)
        note_start(h, (uint8_t)note, vel);
    if (h->stream_events)
        hub_say(h, "ev %d %u on %u %d %u", tile_index(h, t), key, speed, note, vel);
}

/* A tile that is not heard any more: whatever its keys do now, nobody
 * knows. They count as up. */
static void release_tile(hub_t *h, hub_tile_t *t)
{
    for (uint8_t k = 0; k < KEYS_N; k++)
        key_off(h, t, k, 64, 0);
    memset(t->diff, 0, sizeof t->diff);
}

/* The keys stay down in the hub's books, as they are on the tiles: so STATE
 * finds nothing to put right, and a note stays off until its key is pressed
 * again. */
void hub_release_all(hub_t *h)
{
    for (int i = 0; i < HUB_MAX_TILES; i++) {
        hub_tile_t *t = &h->tile[i];
        for (uint8_t k = 0; t->used && k < KEYS_N; k++) {
            if (KEY_BIT(t->on, k) && t->note[k] != NO_NOTE) {
                note_stop(h, t->note[k], 64);
                t->note[k] = NO_NOTE;
            }
        }
    }
}

void hub_panic(hub_t *h)
{
    hub_release_all(h);
    /* nothing should be left; if the count is off, this puts it right */
    for (int n = 0; n < 128; n++) {
        if (h->held[n])
            midi_put(h, 0x80, (uint8_t)n, 64);
        h->held[n] = 0;
    }
    midi_put(h, 0xB0, 123, 0);          /* all notes off, for the synth's sake */
}

/* -------------------------------------------------------------- the table --- */

hub_tile_t *hub_find(hub_t *h, uint32_t id)
{
    for (int i = 0; i < HUB_MAX_TILES; i++) {
        if (h->tile[i].used && h->tile[i].id == id)
            return &h->tile[i];
    }
    return NULL;
}

static hub_tile_t *find_or_add(hub_t *h, uint32_t id)
{
    hub_tile_t *t = hub_find(h, id);

    if (t)
        return t;
    for (int i = 0; i < HUB_MAX_TILES; i++) {
        t = &h->tile[i];
        if (!t->used) {
            memset(t, 0, sizeof *t);
            t->used = true;
            t->id = id;
            t->parent = PORT_NONE;
            t->hops = 0xFF;
            memset(t->note, NO_NOTE, sizeof t->note);
            h->grid_dirty = true;
            return t;
        }
    }
    return NULL;
}

uint32_t hub_root(const hub_t *h)
{
    const link_t *l = &h->node.port[0].link;

    return l->up && l->nbr_kind == KIND_TILE ? l->nbr_id : ID_NONE;
}

/* ------------------------------------------------------ frames from tiles --- */

static void on_note(hub_t *h, hub_tile_t *t, const uint8_t *b)
{
    uint8_t counter = b[0], key = b[1] & 0x7Fu;
    bool on = (b[1] & 0x80u) != 0;
    uint16_t speed = get16(b + 2);

    if (key >= KEYS_N)
        return;
    if (t->have_events) {
        int8_t d = (int8_t)(uint8_t)(counter - t->events);
        if (d <= 0) {
            /* this one, or a later one, was counted already */
            h->stats.notes_old++;
            return;
        }
        h->stats.notes_lost += (uint32_t)(d - 1);
    }
    t->have_events = true;
    t->events = counter;
    t->behind = 0;

    t->diff[key >> 3] &= (uint8_t)~(1u << (key & 7));
    if (on)
        key_on(h, t, key, notes_velocity(&h->set, speed), speed);
    else
        key_off(h, t, key, notes_velocity(&h->set, speed), speed);
}

/* STATE: what the tile holds on, every 100 ms. A key the hub has wrong in
 * two STATEs running is put right: that is what makes a lost NOTE harmless. */
static void on_state(hub_t *h, hub_tile_t *t, const uint8_t *b)
{
    uint8_t counter = b[0];

    if (!t->have_events) {
        t->have_events = true;
        t->events = counter;
    } else if ((int8_t)(uint8_t)(counter - t->events) < 0) {
        /* Older than notes already heard: it was overtaken on the way. If it
         * happens twice running, the tile started again and counts from 0. */
        if (++t->behind < 2)
            return;
        t->events = counter;
    }
    /* A counter ahead of the hub's does not move the hub's: the NOTE it
     * counts may still come, and is welcome. */
    t->behind = 0;

    for (uint8_t i = 0; i < HUB_KEY_BYTES; i++) {
        uint8_t d = b[1 + i] ^ t->on[i];
        uint8_t sure = d & t->diff[i];
        t->diff[i] = d & (uint8_t)~sure;
        for (uint8_t bit = 0; bit < 8 && sure; bit++) {
            uint8_t key = (uint8_t)(8 * i + bit);
            if (!((sure >> bit) & 1u) || key >= KEYS_N)
                continue;
            if ((b[1 + i] >> bit) & 1u) {
                h->stats.healed_on++;
                key_on(h, t, key, notes_velocity_mid(&h->set), 0);
            } else {
                h->stats.healed_off++;
                key_off(h, t, key, 64, 0);
            }
        }
    }
}

static void on_info(hub_t *h, hub_tile_t *t, const uint8_t *b)
{
    bool moved = !t->have_info || t->cols != b[3] || t->rows != b[4];

    t->have_info = true;
    t->version = get16(b);
    t->flags = b[2];
    t->cols = b[3];
    t->rows = b[4];
    t->parent = b[5];
    t->hops = b[6];
    for (uint8_t p = 0; p < TILE_PORTS; p++) {
        uint32_t id = get32(b + 7 + 5 * p);
        uint8_t port = b[7 + 5 * p + 4];
        if (id != t->nbr_id[p] || port != t->nbr_port[p])
            moved = true;
        t->nbr_id[p] = id;
        t->nbr_port[p] = port;
    }
    t->uptime = get16(b + 27);
    t->crc_drops = get16(b + 29);
    t->queue_drops = get16(b + 31);
    if (moved)
        h->grid_dirty = true;
}

/* POS: how far down keys are. Polyphonic aftertouch, if that is wanted. */
static void on_pos(hub_t *h, hub_tile_t *t, const uint8_t *b, uint8_t n)
{
    if (!h->set.aftertouch)
        return;
    for (uint8_t i = 0; i + 1 < n; i += 2) {
        uint8_t key = b[i];
        if (key < KEYS_N && KEY_BIT(t->on, key) && t->note[key] != NO_NOTE)
            midi_put(h, 0xA0, t->note[key], b[i + 1] >> 1);
    }
}

static void on_raw(hub_t *h, hub_tile_t *t, const uint8_t *b, uint8_t n)
{
    if (h->stream_adc != t->id || n < 3 || n < 3 + 2 * b[2])
        return;
    hub_print(h, "adc %d %u %u", tile_index(h, t), b[0], b[1]);
    for (uint8_t i = 0; i < b[2]; i++)
        hub_print(h, " %u", get16(b + 3 + 2 * i));
    hub_say(h, "%s", "");
}

static void on_reply(hub_t *h, hub_tile_t *t, const uint8_t *b, uint8_t n)
{
    hub_job_t *j = &h->job;

    if (j->kind == JOB_NONE || !j->waiting || j->replied)
        return;
    if (j->target[j->at] != t->id || b[0] != j->counter)
        return;
    j->replied = true;
    j->took = since(h->now, j->first_sent);
    j->reply_len = (uint8_t)(n - 1 > (int)sizeof j->reply ? sizeof j->reply : n - 1u);
    memcpy(j->reply, b + 1, j->reply_len);
}

static void on_log(hub_t *h, hub_tile_t *t, const uint8_t *b, uint8_t n)
{
    char text[FRAME_MAX_PAYLOAD + 1];

    if (!h->stream_events)
        return;                 /* the hub says nothing unasked */
    for (uint8_t i = 0; i < n; i++)
        text[i] = b[i] >= 0x20 && b[i] < 0x7F ? (char)b[i] : '.';
    text[n] = 0;
    hub_say(h, "log %d %s", tile_index(h, t), text);
}

static void on_frame(void *ctx, uint8_t port, uint8_t type, const uint8_t *pl, uint8_t len,
                     uint32_t now)
{
    hub_t *h = ctx;
    uint32_t id = get32(pl);
    const uint8_t *b = pl + UP_HEAD_LEN;
    uint8_t n = (uint8_t)(len - UP_HEAD_LEN);

    (void)port;
    h->now = now;
    if (id == ID_NONE || id == ID_ALL || id == h->node.id)
        return;
    hub_tile_t *t = find_or_add(h, id);
    if (!t) {
        h->stats.no_room++;
        return;
    }
    t->last_heard = now;
    t->silent = false;

    switch (type) {
    case FT_NOTE:
        if (n >= NOTE_LEN - UP_HEAD_LEN)
            on_note(h, t, b);
        break;
    case FT_STATE:
        if (n >= STATE_LEN - UP_HEAD_LEN)
            on_state(h, t, b);
        break;
    case FT_INFO:
        if (n >= INFO_LEN - UP_HEAD_LEN)
            on_info(h, t, b);
        break;
    case FT_POS:
        on_pos(h, t, b, n);
        break;
    case FT_RAW:
        on_raw(h, t, b, n);
        break;
    case FT_REPLY:
        if (n >= 2)
            on_reply(h, t, b, n);
        break;
    case FT_LOG:
        on_log(h, t, b, n);
        break;
    default:
        break;
    }
}

/* ---------------------------------------------------------------- commands --- */

/* A counter for a new command: never the one this tile ran last, or it
 * would take the new command for a repeat. */
static uint8_t next_counter(hub_t *h, hub_tile_t *t)
{
    h->counter++;
    if (t) {
        if (t->have_counter && t->last_counter == h->counter)
            h->counter++;
        t->have_counter = true;
        t->last_counter = h->counter;
    } else {
        /* a command for everybody: tiles keep those counters apart */
        if (h->have_bcast && h->last_bcast == h->counter)
            h->counter++;
        h->have_bcast = true;
        h->last_bcast = h->counter;
    }
    return h->counter;
}

static void send_cmd(hub_t *h, uint32_t target, uint8_t counter, uint8_t op,
                     const uint8_t *arg, uint8_t arg_len)
{
    uint8_t pl[CMD_HEAD_LEN + sizeof h->job.arg];

    put32(pl, target);
    pl[4] = counter;
    pl[5] = op;
    if (arg_len)
        memcpy(pl + CMD_HEAD_LEN, arg, arg_len);
    node_send_down(&h->node, FT_CMD, pl, (uint8_t)(CMD_HEAD_LEN + arg_len));
}

void hub_broadcast(hub_t *h, uint8_t op, const uint8_t *arg, uint8_t arg_len)
{
    put32(h->bcast, ID_ALL);
    h->bcast[4] = next_counter(h, NULL);
    h->bcast[5] = op;
    if (arg_len)
        memcpy(h->bcast + CMD_HEAD_LEN, arg, arg_len);
    h->bcast_len = (uint8_t)(CMD_HEAD_LEN + arg_len);
    h->bcast_left = BCAST_REPEATS;
    h->bcast_next = h->now;
}

void hub_job_start(hub_t *h, uint8_t kind, uint8_t op, const uint8_t *arg, uint8_t arg_len)
{
    hub_job_t *j = &h->job;

    j->kind = kind;
    j->stage = STAGE_TILES;
    j->op = op;
    j->arg_len = arg_len;
    if (arg_len)
        memcpy(j->arg, arg, arg_len);
    j->at = 0;
    j->failed = 0;
    j->waiting = false;
}

static void job_finish(hub_t *h)
{
    hub_job_t *j = &h->job;

    if (j->failed == 0)
        hub_say(h, "ok");
    else
        hub_say(h, "error: %u failed", j->failed);
    j->kind = JOB_NONE;
}

/* The tiles are done: what else did the command want? */
static void job_after_tiles(hub_t *h, uint32_t now)
{
    hub_job_t *j = &h->job;

    if (j->then_quiet) {
        j->then_quiet = false;
        if (j->failed == 0) {
            uint8_t arg[2];
            put16(arg, j->quiet_ms);
            hub_broadcast(h, OP_QUIET, arg, sizeof arg);
            node_quiet(&h->node, j->quiet_ms, now);
            return;             /* back here when the copies have gone out */
        }
    }
    if (j->then_flash) {
        j->then_flash = false;
        j->stage = STAGE_FLASH;
        j->flash_done = false;
        j->sent_at = now;
        h->actions |= HUB_ACT_SAVE;
        return;
    }
    job_finish(h);
}

/* One step of the job. True if the next step can follow at once. */
static bool job_step(hub_t *h, uint32_t now)
{
    hub_job_t *j = &h->job;

    if (j->kind == JOB_NONE || h->bcast_left)
        return false;

    if (j->stage == STAGE_FLASH) {
        if (!j->flash_done && since(now, j->sent_at) < FLASH_WAIT_MS)
            return false;
        if (!j->flash_done || !j->flash_ok) {
            hub_say(h, "hub: flash failed");
            j->failed++;
        }
        job_finish(h);
        return false;
    }

    if (j->at >= j->ntargets) {
        job_after_tiles(h, now);
        return false;
    }

    hub_tile_t *t = hub_find(h, j->target[j->at]);
    if (!t) {
        hub_say(h, "tile %08x: gone", (unsigned)j->target[j->at]);
        j->failed++;
        j->waiting = false;
        j->at++;
        return true;
    }
    if (!j->waiting) {
        j->counter = next_counter(h, t);
        j->tries = 1;
        j->waiting = true;
        j->replied = false;
        j->first_sent = j->sent_at = now;
        send_cmd(h, t->id, j->counter, j->op, j->arg, j->arg_len);
        return false;
    }
    if (j->replied) {
        if (j->reply[0] != ST_OK)
            j->failed++;
        console_reply(h, t, j->reply[0], j->reply + 1, (uint8_t)(j->reply_len - 1));
        j->waiting = false;
        j->at++;
        return true;
    }
    if (since(now, j->sent_at) >= CMD_RETRY_MS) {
        if (j->tries >= CMD_TRIES) {
            hub_say(h, "tile %d: no answer", tile_index(h, t));
            h->stats.no_answer++;
            j->failed++;
            j->waiting = false;
            j->at++;
            return true;
        }
        j->tries++;
        j->sent_at = now;
        send_cmd(h, t->id, j->counter, j->op, j->arg, j->arg_len);
    }
    return false;
}

/* ---------------------------------------------------------------- hardware --- */

unsigned hub_actions(hub_t *h)
{
    unsigned a = h->actions;

    h->actions = 0;
    return a;
}

void hub_save_image(const hub_t *h, uint8_t out[STORE_HUB_LEN])
{
    store_hub_pack(&h->set, out);
}

void hub_save_done(hub_t *h, bool ok)
{
    if (h->job.kind != JOB_NONE && h->job.stage == STAGE_FLASH) {
        h->job.flash_done = true;
        h->job.flash_ok = ok;
    }
}

void hub_bridge_end(hub_t *h, uint32_t now)
{
    if (!h->bridging)
        return;
    h->bridging = false;
    h->line_len = 0;
    h->line_over = false;
    node_resume(&h->node, now);
}

/* ------------------------------------------------------------- the program --- */

void hub_init(hub_t *h, const uint8_t uid[12], const uint8_t *record, size_t record_len,
              uint32_t now)
{
    hub_settings_t s;

    memset(h, 0, sizeof *h);
    node_init(&h->node, KIND_HUB, 1, id_from_uid(uid), on_frame, h, now);
    settings_default(&h->set);
    if (store_hub_unpack(record, record_len, &s) && settings_ok(&s))
        h->set = s;
    power_init(&h->power, h->set.pd_volts, now);
    h->now = now;
    h->started = now;
    /* Tiles forget the last hub's counters before this one's first beacon;
     * starting somewhere random is for the tile that did not. */
    h->counter = (uint8_t)rng_next(&h->node.rng);
}

void hub_rx(hub_t *h, const uint8_t *data, size_t len, uint32_t now)
{
    h->now = now;
    if (!h->bridging)
        node_rx(&h->node, 0, data, len, now);
}

void hub_tick(hub_t *h, uint32_t now)
{
    h->now = now;
    node_tick(&h->node, now);
    if (h->node.changed) {
        h->node.changed = false;        /* another tile on the cable, or none */
        h->grid_dirty = true;
    }

    for (int i = 0; i < HUB_MAX_TILES; i++) {
        hub_tile_t *t = &h->tile[i];
        if (!t->used)
            continue;
        uint32_t age = since(now, t->last_heard);
        if (!t->silent && age >= TILE_SILENT_MS) {
            /* unplugged, most likely: no note may hang */
            t->silent = true;
            t->have_events = false;     /* it may come back as a tile that started again */
            t->behind = 0;
            h->stats.silent++;
            release_tile(h, t);
        }
        if (age >= TILE_FORGET_MS) {
            release_tile(h, t);
            if (h->stream_adc == t->id)
                h->stream_adc = 0;
            t->used = false;
            h->grid_dirty = true;
        }
    }

    if (h->grid_dirty) {
        h->grid_dirty = false;
        grid_layout(h);
    }

    if (h->bcast_left && reached(now, h->bcast_next)) {
        node_send_down(&h->node, FT_CMD, h->bcast, h->bcast_len);
        h->bcast_left--;
        h->bcast_next = now + BCAST_GAP_MS;
    }
    while (job_step(h, now))
        ;

    if (h->later && reached(now, h->later_at)) {
        h->actions |= h->later;
        h->later = 0;
    }
}
