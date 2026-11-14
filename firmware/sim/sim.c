#include "sim.h"

#include "lut.h"

#include <string.h>

/* ------------------------------------------------------------------ nodes --- */

static sim_node_t *node_of(sim_t *s, int i)
{
    return i == SIM_HUB ? &s->hub.n : &s->tile[i].n;
}

static uint8_t ports_of(int i)
{
    return i == SIM_HUB ? 1 : TILE_PORTS;
}

static sim_port_t *port_of(sim_t *s, int i, uint8_t p)
{
    return i == SIM_HUB ? &s->hub.port[0] : &s->tile[i].port[p];
}

static bool running(const sim_node_t *n)
{
    return n->powered && !n->halted;
}

static bool busy(const sim_t *s, const sim_node_t *n)
{
    return s->t < n->busy_until;
}

static uint32_t local_ms(const sim_t *s, const sim_node_t *n)
{
    return (uint32_t)((s->t + n->phase) / SIM_STEPS_PER_MS) + n->clock;
}

uint32_t sim_now(const sim_t *s, int node)
{
    return local_ms(s, node == SIM_HUB ? &s->hub.n : &s->tile[node].n);
}

static void ports_clear(sim_t *s, int i)
{
    for (uint8_t p = 0; p < ports_of(i); p++) {
        sim_port_t *sp = port_of(s, i, p);
        sp->swapped = false;
        sp->tx.len = sp->tx.sent = 0;
        sp->rx_len = 0;
    }
}

/* The program starts: from flash if there is a record, with a clock that
 * starts anywhere. */
static void boot(sim_t *s, int i)
{
    sim_node_t *n = node_of(s, i);

    n->powered = true;
    n->halted = false;
    n->boot_at = 0;
    n->busy_until = 0;
    n->saving = false;
    n->clock = rng_next(&s->rng);
    if (s->wrap_ms)             /* its clock goes round to 0 this long after it starts */
        n->clock = 0u - s->wrap_ms - (uint32_t)(s->t / SIM_STEPS_PER_MS);
    n->phase = (uint8_t)rng_range(&s->rng, 0, SIM_STEPS_PER_MS - 1);
    ports_clear(s, i);

    if (i == SIM_HUB) {
        hub_init(&s->hub.h, n->uid, n->have_flash ? s->hub.flash : NULL,
                 n->have_flash ? STORE_HUB_LEN : 0, local_ms(s, n));
        s->ntyped = 0;
    } else {
        sim_tile_t *st = &s->tile[i];
        st->scan = 0;
        tile_init(&st->t, n->uid, n->have_flash ? st->flash : NULL,
                  n->have_flash ? STORE_TILE_LEN : 0, SIM_SCAN_HZ, local_ms(s, n));
    }
}

static void stop(sim_t *s, int i)
{
    sim_node_t *n = node_of(s, i);

    if (n->saving)
        n->have_flash = false;  /* cut off half way: the sector is erased or half written */
    n->powered = false;
    n->halted = false;
    n->saving = false;
    n->busy_until = 0;
    n->boot_at = 0;
    ports_clear(s, i);
}

static void reboot(sim_t *s, int i)
{
    stop(s, i);
    node_of(s, i)->reboots++;
    node_of(s, i)->boot_at = s->t + SIM_BOOT_MS * SIM_STEPS_PER_MS;
}

void sim_power(sim_t *s, int node, bool on)
{
    sim_node_t *n = node_of(s, node);

    if (!on)
        stop(s, node);
    else if (!n->powered && !n->boot_at)
        n->boot_at = s->t + SIM_BOOT_MS * SIM_STEPS_PER_MS;
}

static void uid_make(sim_t *s, uint8_t uid[12])
{
    for (int i = 0; i < 12; i++)
        uid[i] = (uint8_t)rng_next(&s->rng);
}

void sim_init(sim_t *s, uint32_t seed)
{
    memset(s, 0, sizeof *s);
    rng_seed(&s->rng, seed);
    s->hub.port[0].cable = -1;
    uid_make(s, s->hub.n.uid);
    boot(s, SIM_HUB);
}

int sim_add_tile(sim_t *s)
{
    if (s->ntiles == SIM_MAX_TILES)
        return -1;
    int i = s->ntiles++;
    sim_tile_t *st = &s->tile[i];

    memset(st, 0, sizeof *st);
    for (uint8_t p = 0; p < TILE_PORTS; p++)
        st->port[p].cable = -1;
    uid_make(s, st->n.uid);
    st->scan_phase = (uint8_t)rng_range(&s->rng, 0, SIM_SCAN_STEPS - 1);
    for (int k = 0; k < KEYS_N; k++) {
        int spread = (int)rng_range(&s->rng, 0, 2u * (unsigned)s->rest_spread) - s->rest_spread;
        int pct = (int)rng_range(&s->rng, 0, 2u * (unsigned)s->swing_spread) - s->swing_spread;
        st->rest[k] = (uint16_t)(2000 + spread);
        st->swing[k] = (int16_t)(600 * (100 + pct) / 100);
    }
    boot(s, i);
    return i;
}

/* ----------------------------------------------------------------- cables --- */

int sim_cable(sim_t *s, int a, uint8_t a_port, int b, uint8_t b_port, bool crossed)
{
    sim_port_t *pa = port_of(s, a, a_port), *pb = port_of(s, b, b_port);

    if (s->ncables == SIM_MAX_CABLES || pa->cable >= 0 || pb->cable >= 0 || pa == pb)
        return -1;
    int i = s->ncables++;
    sim_cable_t *c = &s->cable[i];

    memset(c, 0, sizeof *c);
    c->used = c->plugged = true;
    c->crossed = crossed;
    c->node[0] = a;
    c->port[0] = a_port;
    c->node[1] = b;
    c->port[1] = b_port;
    pa->cable = pb->cable = i;
    pa->end = 0;
    pb->end = 1;
    return i;
}

void sim_unplug(sim_t *s, int cable)
{
    s->cable[cable].plugged = false;
}

void sim_plug(sim_t *s, int cable)
{
    s->cable[cable].plugged = true;
}

void sim_drop(sim_t *s, int cable, int end, uint8_t type, unsigned count)
{
    s->cable[cable].drop_type[end] = type;
    s->cable[cable].drop_left[end] = (uint16_t)count;
}

/* A port not swapped sends on pin A and listens on pin B; a swapped one the
 * other way round. So over a cable that crosses A and B two ports hear each
 * other when they are the same way round, over one that does not, when they
 * differ. (Otherwise both send into the same wire and both listen to the
 * other, silent, one.) */
static bool hears(const sim_cable_t *c, const sim_port_t *rx, const sim_port_t *tx)
{
    return (rx->swapped != tx->swapped) != c->crossed;
}

/* The other end of the port's cable, if it is plugged in. */
static sim_port_t *far_end(sim_t *s, const sim_port_t *p, int *node, uint8_t *port)
{
    if (p->cable < 0 || !s->cable[p->cable].plugged)
        return NULL;
    const sim_cable_t *c = &s->cable[p->cable];
    *node = c->node[!p->end];
    *port = c->port[!p->end];
    return port_of(s, *node, *port);
}

int sim_toward_hub(const sim_t *s, int tile)
{
    uint8_t parent = s->tile[tile].t.node.parent;

    if (parent == PORT_NONE)
        return SIM_NOBODY;
    const sim_port_t *p = &s->tile[tile].port[parent];
    if (p->cable < 0 || !s->cable[p->cable].plugged)
        return SIM_NOBODY;
    return s->cable[p->cable].node[!p->end];
}

/* ----------------------------------------------------------------- frames --- */

/* The transmitter is free: turn the pins round if the link wants them
 * round, and take the next frame. */
static void start_frame(sim_t *s, int i, uint8_t pi)
{
    sim_port_t *p = port_of(s, i, pi);
    sim_tx_t *tx = &p->tx;
    size_t n;

    if (i == SIM_HUB) {
        p->swapped = hub_swapped(&s->hub.h);
        n = hub_tx(&s->hub.h, tx->wire);
    } else {
        p->swapped = tile_swapped(&s->tile[i].t, pi);
        n = tile_tx(&s->tile[i].t, pi, tx->wire);
    }
    if (!n)
        return;
    tx->len = (uint8_t)n;
    tx->sent = 0;
    tx->lost = false;
    tx->whole = true;

    /* what the cable does to it */
    int j;
    uint8_t pj;
    sim_port_t *q = far_end(s, p, &j, &pj);
    if (!q || !running(node_of(s, j)) || !hears(&s->cable[p->cable], q, p))
        return;                 /* nobody would hear it anyway */
    sim_cable_t *c = &s->cable[p->cable];
    int d = p->end;
    if (c->drop_left[d] && (c->drop_type[d] == 0 || c->drop_type[d] == tx->wire[1])) {
        c->drop_left[d]--;      /* wire[1] is the type: it is never 0, so COBS leaves it alone */
        tx->lost = true;
    } else if (c->loss[d] && rng_range(&s->rng, 0, 999) < c->loss[d]) {
        tx->lost = true;
    } else if (c->corrupt[d] && rng_range(&s->rng, 0, 999) < c->corrupt[d]) {
        tx->wire[rng_range(&s->rng, 0, (uint32_t)n - 1)] ^= (uint8_t)(1u << rng_range(&s->rng, 0, 7));
    }
    if (tx->lost)
        c->lost[d]++;
}

/* Up to `room` bytes of the port's frame go down the cable. Returns how
 * many went. */
static int carry(sim_t *s, int i, uint8_t pi, int room)
{
    sim_port_t *p = port_of(s, i, pi);
    sim_tx_t *tx = &p->tx;
    int j = SIM_NOBODY, k = 0;
    uint8_t pj = 0;

    sim_port_t *q = far_end(s, p, &j, &pj);
    sim_cable_t *c = p->cable >= 0 ? &s->cable[p->cable] : NULL;

    for (; k < room && tx->sent < tx->len; k++) {
        uint8_t byte = tx->wire[tx->sent++];
        if (!q || tx->lost || !running(node_of(s, j)) || !hears(c, q, p)) {
            tx->whole = false;
        } else if (q->rx_len == SIM_RX_MAX) {
            node_of(s, j)->rx_overruns++;
            tx->whole = false;
        } else {
            q->rx[q->rx_len++] = byte;
        }
    }
    if (tx->sent < tx->len)
        return k;

    /* the frame is out */
    if (c)
        c->sent[p->end]++;
    if (c && tx->whole) {
        c->arrived[p->end]++;
        if (s->tap) {
            frame_rx_t rx;
            frame_result_t res = FRAME_NONE;
            uint8_t type = 0, len = 0, pl[FRAME_MAX_PAYLOAD];

            frame_rx_reset(&rx);
            for (uint8_t k = 0; k < tx->len; k++)
                res = frame_rx_byte(&rx, tx->wire[k], &type, pl, &len);
            if (res == FRAME_OK)
                s->tap(s->tap_ctx, i, pi, j, pj, type, pl, len);
        }
    }
    tx->len = tx->sent = 0;
    return k;
}

/* One step of a port's transmitter: ten bytes' worth of time. A frame that
 * ends in the middle of it is followed by the next at once. While the
 * program writes flash, the frame on its way goes on and no new one starts. */
static void transmit(sim_t *s, int i, uint8_t pi)
{
    sim_node_t *n = node_of(s, i);
    sim_port_t *p = port_of(s, i, pi);
    int room = SIM_STEP_BYTES;

    while (room > 0) {
        if (p->tx.len == 0) {
            if (!running(n) || busy(s, n))
                return;
            start_frame(s, i, pi);
            if (p->tx.len == 0)
                return;
        }
        room -= carry(s, i, pi, room);
    }
}

/* ------------------------------------------------------------------- keys --- */

void sim_move(sim_t *s, int tile, int key, int to, uint32_t ms)
{
    sim_tile_t *st = &s->tile[tile];

    if (ms == 0) {
        st->x[key] = to;
        st->moving[key] = false;
        return;
    }
    st->from[key] = st->x[key];
    st->to[key] = to;
    st->t0[key] = s->t;
    st->t1[key] = s->t + (uint64_t)ms * SIM_STEPS_PER_MS;
    st->moving[key] = true;
}

static void scan(sim_t *s, int i)
{
    sim_tile_t *st = &s->tile[i];
    uint16_t adc[KEYS_N];

    for (int k = 0; k < KEYS_N; k++) {
        if (st->moving[k]) {
            if (s->t >= st->t1[k]) {
                st->x[k] = st->to[k];
                st->moving[k] = false;
            } else {
                st->x[k] = st->from[k] + (int32_t)((int64_t)(st->to[k] - st->from[k]) *
                           (int64_t)(s->t - st->t0[k]) / (int64_t)(st->t1[k] - st->t0[k]));
            }
        }
        int v = st->rest[k] + st->swing[k] * (int)lut_apply(lut_field, (uint16_t)st->x[k]) / 65535;
        if (s->noise)
            v += (int)rng_range(&s->rng, 0, 2u * (unsigned)s->noise) - s->noise;
        adc[k] = (uint16_t)(v < 0 ? 0 : v > 4095 ? 4095 : v);
    }
    st->scan++;                 /* the ADC goes on while the program is busy; those scans are lost */
    if (running(&st->n) && !busy(s, &st->n))
        tile_scan(&st->t, st->scan, adc, local_ms(s, &st->n));
}

/* ------------------------------------------------------- flash and restart --- */

static void save_begin(sim_t *s, int i)
{
    sim_node_t *n = node_of(s, i);

    if (i == SIM_HUB)
        hub_save_image(&s->hub.h, s->hub.image);
    else
        tile_save_image(&s->tile[i].t, s->tile[i].image);
    n->saving = true;
    n->busy_until = s->t + SIM_FLASH_MS * SIM_STEPS_PER_MS;
}

static void save_end(sim_t *s, int i)
{
    sim_node_t *n = node_of(s, i);
    bool ok = !n->flash_fails;

    n->saving = false;
    n->saves++;
    n->have_flash = ok;         /* a write that failed leaves no record */
    if (i == SIM_HUB) {
        if (ok)
            memcpy(s->hub.flash, s->hub.image, STORE_HUB_LEN);
        hub_save_done(&s->hub.h, ok);
    } else {
        if (ok)
            memcpy(s->tile[i].flash, s->tile[i].image, STORE_TILE_LEN);
        tile_save_done(&s->tile[i].t, ok);
    }
}

static void halt(sim_t *s, int i)
{
    node_of(s, i)->halted = true;
    node_of(s, i)->bootloaders++;
    ports_clear(s, i);
}

/* ------------------------------------------------------------------ ticks --- */

static void tick_tile(sim_t *s, int i)
{
    sim_tile_t *st = &s->tile[i];

    tile_tick(&st->t, local_ms(s, &st->n));
    unsigned a = tile_actions(&st->t);
    if (a & TILE_ACT_SAVE)
        save_begin(s, i);
    if (a & TILE_ACT_BOOTLOADER)
        halt(s, i);
    else if (a & TILE_ACT_REBOOT)
        reboot(s, i);
}

static void tick_hub(sim_t *s)
{
    hub_t *h = &s->hub.h;
    uint32_t now = local_ms(s, &s->hub.n);
    uint8_t m[3], buf[256];
    size_t n;

    /* what was typed; the hub may leave some for later */
    if (s->ntyped) {
        n = hub_console_rx(h, (const uint8_t *)s->typed, s->ntyped, now);
        memmove(s->typed, s->typed + n, s->ntyped - n);
        s->ntyped -= n;
    }
    hub_tick(h, now);

    while (hub_midi_get(h, m)) {
        if (s->nmidi < SIM_MIDI_MAX) {
            memcpy(s->midi[s->nmidi], m, 3);
            s->midi_t[s->nmidi++] = s->t;
        }
    }
    while ((n = hub_console_read(h, buf, sizeof buf)) != 0) {
        if (s->ntext + n < SIM_TEXT_MAX) {
            memcpy(s->text + s->ntext, buf, n);
            s->ntext += n;
            s->text[s->ntext] = 0;
        }
    }

    unsigned a = hub_actions(h);
    if (a & HUB_ACT_BRIDGE)
        s->hub.bridges++;
    if (a & HUB_ACT_SAVE)
        save_begin(s, SIM_HUB);
    if (a & HUB_ACT_BOOTLOADER)
        halt(s, SIM_HUB);
    else if (a & HUB_ACT_REBOOT)
        reboot(s, SIM_HUB);
}

void sim_type(sim_t *s, const char *text)
{
    size_t n = strlen(text);

    if (s->ntyped + n > SIM_TYPED_MAX)
        n = SIM_TYPED_MAX - s->ntyped;
    memcpy(s->typed + s->ntyped, text, n);
    s->ntyped += n;
}

/* ------------------------------------------------------------------- time --- */

static void step(sim_t *s)
{
    s->t++;

    for (int i = SIM_HUB; i < s->ntiles; i++) {
        sim_node_t *n = node_of(s, i);
        if (!n->powered && n->boot_at && s->t >= n->boot_at)
            boot(s, i);
        if (n->saving && s->t >= n->busy_until)
            save_end(s, i);
    }

    /* the cables */
    for (int i = SIM_HUB; i < s->ntiles; i++) {
        for (uint8_t p = 0; p < ports_of(i); p++)
            transmit(s, i, p);
    }

    /* what arrived goes to the programs */
    for (int i = SIM_HUB; i < s->ntiles; i++) {
        sim_node_t *n = node_of(s, i);
        for (uint8_t p = 0; p < ports_of(i); p++) {
            sim_port_t *sp = port_of(s, i, p);
            if (!running(n)) {
                sp->rx_len = 0;
            } else if (sp->rx_len && !busy(s, n)) {
                if (i == SIM_HUB)
                    hub_rx(&s->hub.h, sp->rx, sp->rx_len, local_ms(s, n));
                else
                    tile_rx(&s->tile[i].t, p, sp->rx, sp->rx_len, local_ms(s, n));
                sp->rx_len = 0;
            }
        }
    }

    for (int i = 0; i < s->ntiles; i++) {
        if ((s->t + s->tile[i].scan_phase) % SIM_SCAN_STEPS == 0)
            scan(s, i);
    }

    for (int i = SIM_HUB; i < s->ntiles; i++) {
        sim_node_t *n = node_of(s, i);
        if (!running(n) || busy(s, n) || (s->t + n->phase) % SIM_STEPS_PER_MS != 0)
            continue;
        if (i == SIM_HUB)
            tick_hub(s);
        else
            tick_tile(s, i);
    }
}

void sim_run(sim_t *s, uint32_t ms)
{
    for (uint64_t n = (uint64_t)ms * SIM_STEPS_PER_MS; n; n--)
        step(s);
}
