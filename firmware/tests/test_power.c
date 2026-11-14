/* power.c and ch224.c: the policy on a bench with a charger, a computer,
 * two eFuses and something to short the output with. */
#include "ch224.h"
#include "power.h"
#include "t.h"

typedef struct {
    power_t p;
    power_out_t out;
    uint32_t now;

    /* the charger */
    bool charger;               /* plugged in */
    bool gives[21];             /* the voltages it can give, besides 5 V */
    int charger_mv;
    uint8_t told;               /* what it was last asked for */
    uint32_t told_at;

    /* the computer */
    bool host, configured, suspended;
    int cc1, cc2;

    /* the output */
    bool shorted;
    int held_mv;                /* the tiles' capacitors hold the output up at this */
    uint32_t on_since[3];       /* when each path's eFuse was switched on */

    /* what happened */
    unsigned on_ms[3];          /* milliseconds each eFuse was on */
    unsigned dark_ms;           /* milliseconds with no eFuse on */
    uint8_t asks[8];            /* the voltages asked for, in order */
    unsigned nasks;
    int vin;
} bench_t;

static void bench_init(bench_t *b, uint8_t volts)
{
    memset(b, 0, sizeof *b);
    b->now = 5000;
    power_init(&b->p, volts, b->now);
}

/* One millisecond. */
static void step(bench_t *b)
{
    power_in_t in;

    memset(&in, 0, sizeof in);
    b->now++;

    /* the charger does what it was asked 300 ms later, if it can */
    if (!b->charger) {
        b->charger_mv = 0;
        b->told = 0;
    } else {
        if (b->charger_mv == 0)
            b->charger_mv = 5000;
        if (b->told != b->out.pd_volts) {
            b->told = b->out.pd_volts;
            b->told_at = b->now;
            if (b->nasks < 8)
                b->asks[b->nasks++] = b->told;
        }
        if (since(b->now, b->told_at) == 300)
            b->charger_mv = b->told <= 20 && b->gives[b->told] ? b->told * 1000 : 5000;
    }
    in.vpd_mv = (uint16_t)b->charger_mv;
    in.vhost_mv = b->host ? 5000 : 0;
    in.pd_good = b->charger_mv > 5000;
    in.usb_configured = b->host && b->configured;
    in.usb_suspended = b->suspended;
    in.cc1_mv = (uint16_t)b->cc1;
    in.cc2_mv = (uint16_t)b->cc2;

    /* the two eFuses: the higher input wins; a short pulls the output down
     * and, 18 ms later, raises the flag; an output held above an input
     * raises that path's flag too (reverse blocking) */
    int vin = 0;
    if (b->out.en_pd && in.vpd_mv > vin)
        vin = in.vpd_mv;
    if (b->out.en_host && in.vhost_mv > vin)
        vin = in.vhost_mv;
    if (b->shorted) {
        vin = vin ? 300 : 0;
        in.flt_pd = b->out.en_pd && since(b->now, b->on_since[PWR_PD]) >= 18;
        in.flt_host = b->out.en_host && since(b->now, b->on_since[PWR_HOST]) >= 18;
    } else {
        if (vin < b->held_mv)
            vin = b->held_mv;
        in.flt_pd = b->out.en_pd && vin > in.vpd_mv;
        in.flt_host = b->out.en_host && vin > in.vhost_mv;
    }
    in.vin_mv = (uint16_t)vin;
    b->vin = vin;

    bool was_pd = b->out.en_pd, was_host = b->out.en_host;
    power_step(&b->p, &in, b->now, &b->out);
    if (b->out.en_pd && !was_pd)
        b->on_since[PWR_PD] = b->now;
    if (b->out.en_host && !was_host)
        b->on_since[PWR_HOST] = b->now;
    b->on_ms[PWR_PD] += b->out.en_pd;
    b->on_ms[PWR_HOST] += b->out.en_host;
    b->dark_ms += !b->out.en_pd && !b->out.en_host;
}

static void run(bench_t *b, unsigned ms)
{
    while (ms--)
        step(b);
}

/* Run until the charger's path comes on; how long that took. */
static unsigned until_pd(bench_t *b, unsigned limit)
{
    unsigned n = 0;

    while (!b->out.en_pd && n < limit) {
        step(b);
        n++;
    }
    return n;
}

static void nothing_plugged(void)
{
    bench_t b;

    bench_init(&b, 15);
    run(&b, 3000);
    CHECK(!b.out.en_pd && !b.out.en_host && !b.out.ilim_hi);
    CHECK_EQ(b.out.pd_volts, 15);
    CHECK(strcmp(power_state_name(&b.p), "off") == 0);
}

static void charger_15v(void)
{
    bench_t b;

    bench_init(&b, 15);
    b.gives[15] = b.gives[12] = b.gives[9] = true;
    b.charger = true;
    run(&b, 100);
    CHECK(!b.out.en_pd);                        /* still 5 V: wait for it */
    CHECK(strcmp(power_state_name(&b.p), "pd wait") == 0);
    unsigned t = 100 + until_pd(&b, 5000);
    CHECK(t >= 300 + PWR_PD_STABLE_MS && t <= 300 + PWR_PD_STABLE_MS + 5);
    run(&b, 200);
    CHECK(b.out.en_pd && !b.out.en_host);
    CHECK_EQ(b.vin, 15000);
    CHECK_EQ(b.nasks, 1);
    CHECK_EQ(b.asks[0], 15);
    CHECK(strcmp(power_state_name(&b.p), "pd") == 0);

    /* the computer being there too changes nothing: the charger is preferred */
    b.host = b.configured = true;
    run(&b, 500);
    CHECK(b.out.en_pd && !b.out.en_host);

    /* charger pulled: the computer takes over; pushed back in: the charger again */
    b.charger = false;
    run(&b, 5);
    CHECK(!b.out.en_pd && b.out.en_host);
    CHECK(strcmp(power_state_name(&b.p), "host") == 0);
    CHECK_EQ(b.p.tries, 0);                     /* pulling a plug is not a fault */
    run(&b, 1000);
    b.charger = true;
    b.dark_ms = 0;
    until_pd(&b, 5000);
    CHECK(b.out.en_pd && b.out.en_host);        /* both, for a moment */
    run(&b, PWR_OVERLAP_MS + 2);
    CHECK(b.out.en_pd && !b.out.en_host);
    CHECK_EQ(b.dark_ms, 0);                     /* the tiles never lost power */
    CHECK_EQ(b.p.tries, 0);
}

/* A charger with no 15 V: the hub asks down the list. */
static void charger_lower(void)
{
    bench_t b;

    bench_init(&b, 15);
    b.gives[9] = true;
    b.charger = true;
    unsigned t = until_pd(&b, 10000);
    CHECK(t >= 2 * PWR_PD_STEP_MS + 300 && t <= 2 * PWR_PD_STEP_MS + 300 + PWR_PD_STABLE_MS + 5);
    CHECK_EQ(b.nasks, 3);
    CHECK_EQ(b.asks[0], 15);
    CHECK_EQ(b.asks[1], 12);
    CHECK_EQ(b.asks[2], 9);
    run(&b, 100);
    CHECK_EQ(b.vin, 9000);
    CHECK(strcmp(power_state_name(&b.p), "pd") == 0);

    /* 12 V on offer: taken at the second ask */
    bench_init(&b, 15);
    b.gives[12] = b.gives[9] = true;
    b.charger = true;
    until_pd(&b, 10000);
    CHECK_EQ(b.nasks, 2);
    run(&b, 100);
    CHECK_EQ(b.vin, 12000);

    /* settings that want 9 V do not ask for more */
    bench_init(&b, 9);
    b.gives[15] = b.gives[12] = b.gives[9] = true;
    b.charger = true;
    until_pd(&b, 10000);
    CHECK_EQ(b.nasks, 1);
    CHECK_EQ(b.asks[0], 9);
    run(&b, 100);
    CHECK_EQ(b.vin, 9000);
}

/* A plain 5 V supply: after asking for everything, use it as it is. */
static void charger_stuck_at_5v(void)
{
    bench_t b;

    bench_init(&b, 15);
    b.charger = true;
    run(&b, 3 * PWR_PD_STEP_MS - 10);
    CHECK(!b.out.en_pd);
    CHECK_EQ(b.nasks, 3);
    unsigned t = until_pd(&b, 1000);
    CHECK(t <= 12);
    run(&b, 500);
    CHECK(b.out.en_pd && !b.out.en_host);
    CHECK_EQ(b.vin, 5000);
    CHECK_EQ(b.out.pd_volts, 9);                /* the last thing asked for stays asked */
    CHECK_EQ(b.p.tries, 0);

    /* meanwhile the computer may feed the tiles, and hands over cleanly */
    bench_init(&b, 15);
    b.host = b.configured = true;
    run(&b, 50);
    CHECK(b.out.en_host);
    b.charger = true;
    b.dark_ms = 0;
    run(&b, 3 * PWR_PD_STEP_MS - 10);
    CHECK(b.out.en_host && !b.out.en_pd);
    run(&b, 100);
    CHECK(b.out.en_pd && !b.out.en_host);
    CHECK_EQ(b.dark_ms, 0);

    /* settings that want 5 V get it at once */
    bench_init(&b, 5);
    b.gives[15] = true;
    b.charger = true;
    t = until_pd(&b, 1000);
    CHECK(t <= PWR_PD_STABLE_MS + 5);
    CHECK_EQ(b.asks[0], 5);
}

/* More than the board can take: the path stays off, and that is no fault. */
static void charger_too_high(void)
{
    bench_t b;

    bench_init(&b, 15);
    b.gives[15] = true;
    b.charger = true;
    until_pd(&b, 5000);
    run(&b, 100);
    CHECK(b.out.en_pd);
    b.charger_mv = 20000;
    run(&b, 50);
    CHECK(!b.out.en_pd && !b.out.en_host);
    CHECK_EQ(b.p.tries, 0);
    b.charger_mv = 15000;
    run(&b, 50);
    CHECK(b.out.en_pd);
}

static void computer_only(void)
{
    bench_t b;

    bench_init(&b, 15);
    b.host = true;
    run(&b, 500);
    CHECK(!b.out.en_host);                      /* not configured yet */
    b.configured = true;
    run(&b, 2);
    CHECK(b.out.en_host && !b.out.en_pd);
    CHECK(!b.out.ilim_hi);
    CHECK_EQ(power_host_offer_ma(&b.p), 500);

    /* the three offers a Type-C socket can make, on either CC pin */
    static const struct { int mv; bool hi; int ma; } level[] = {
        { 0, false, 500 }, { 410, false, 500 }, { 650, false, 500 },
        { 670, true, 1500 }, { 920, true, 1500 }, { 1220, true, 1500 },
        { 1240, true, 3000 }, { 1680, true, 3000 },
    };
    for (unsigned i = 0; i < sizeof level / sizeof level[0]; i++) {
        for (int pin = 0; pin < 2; pin++) {
            b.cc1 = pin == 0 ? level[i].mv : 0;
            b.cc2 = pin == 1 ? level[i].mv : 0;
            run(&b, PWR_CC_STABLE_MS + 2);
            CHECK_EQ(b.out.ilim_hi, level[i].hi);
            CHECK_EQ(power_host_offer_ma(&b.p), level[i].ma);
            CHECK(b.out.en_host);
            b.cc1 = b.cc2 = 0;
            run(&b, 1);
            CHECK(!b.out.ilim_hi);              /* a smaller offer counts at once */
        }
    }

    /* an offer that flickers is not taken */
    for (int i = 0; i < 400; i++) {
        b.cc1 = (i % 50) < 45 ? 900 : 300;
        run(&b, 1);
        CHECK(!b.out.ilim_hi);
    }

    /* unplugged: off */
    b.host = false;
    run(&b, 2);
    CHECK(!b.out.en_host);
    CHECK_EQ(b.p.tries, 0);
}

static void suspend(void)
{
    bench_t b;

    bench_init(&b, 15);
    b.host = b.configured = true;
    run(&b, 100);
    CHECK(b.out.en_host);
    b.suspended = true;
    run(&b, 2);
    CHECK(!b.out.en_host);
    CHECK(strcmp(power_state_name(&b.p), "off") == 0);
    run(&b, 5000);
    CHECK(!b.out.en_host);
    b.suspended = false;
    run(&b, 2);
    CHECK(b.out.en_host);
    CHECK_EQ(b.p.tries, 0);

    /* the computer takes the configuration away (a bus reset): off too */
    b.configured = false;
    run(&b, 2);
    CHECK(!b.out.en_host);

    /* a suspended computer does not stop the charger's path */
    b.configured = true;
    b.suspended = true;
    b.gives[15] = true;
    b.charger = true;
    until_pd(&b, 5000);
    run(&b, 100);
    CHECK(b.out.en_pd && !b.out.en_host);
}

static void fault_and_retry(void)
{
    bench_t b;

    bench_init(&b, 15);
    b.gives[15] = true;
    b.charger = true;
    until_pd(&b, 5000);
    run(&b, 500);

    /* a short: off within the flag's delay, then a try every second */
    b.shorted = true;
    unsigned n = 0;
    while (b.out.en_pd && n < 1000) {
        step(&b);
        n++;
    }
    CHECK(n <= PWR_LOW_MS + 2);
    CHECK_EQ(b.p.tries, 1);
    CHECK_EQ(b.p.fault, PWR_FAULT_LOW);
    CHECK_EQ(b.p.fault_path, PWR_PD);
    CHECK(strcmp(power_state_name(&b.p), "retry") == 0);

    b.on_ms[PWR_PD] = 0;
    run(&b, PWR_RETRY_MS - 5);
    CHECK_EQ(b.on_ms[PWR_PD], 0);               /* off for the whole second */
    run(&b, 10);
    CHECK(b.out.en_pd);                         /* second try */
    run(&b, 200);
    CHECK(!b.out.en_pd);
    CHECK_EQ(b.p.tries, 2);
    CHECK_EQ(b.p.fault, PWR_FAULT_FLAG);        /* at switch-on the flag comes before VIN counts as low */
    run(&b, PWR_RETRY_MS);
    run(&b, 200);
    CHECK_EQ(b.p.tries, 3);
    CHECK(strcmp(power_state_name(&b.p), "locked") == 0);

    /* locked: nothing more, however long */
    b.on_ms[PWR_PD] = 0;
    run(&b, 20000);
    CHECK_EQ(b.on_ms[PWR_PD], 0);
    CHECK(!b.out.en_host);

    /* the short goes away, but locked stays locked until someone acts */
    b.shorted = false;
    run(&b, 3000);
    CHECK(!b.out.en_pd);
    power_command(&b.p, PWR_CMD_RETRY, b.now);
    run(&b, 5);
    CHECK(b.out.en_pd);
    CHECK_EQ(b.p.tries, 0);

    /* one fault, then fine for ten seconds: the count starts again */
    b.shorted = true;
    run(&b, 100);
    b.shorted = false;
    CHECK_EQ(b.p.tries, 1);
    run(&b, PWR_RETRY_MS + 100);
    CHECK(b.out.en_pd);
    CHECK_EQ(b.p.tries, 1);
    run(&b, PWR_HEALTHY_MS);
    CHECK_EQ(b.p.tries, 0);

    /* locked is also undone by pulling the plug */
    b.shorted = true;
    run(&b, 3 * (PWR_RETRY_MS + 300));
    CHECK(strcmp(power_state_name(&b.p), "locked") == 0);
    b.charger = false;
    run(&b, 10);
    CHECK(strcmp(power_state_name(&b.p), "off") == 0);
    b.shorted = false;
    b.charger = true;
    until_pd(&b, 5000);
    CHECK(b.out.en_pd);
    CHECK_EQ(b.p.tries, 0);
}

/* The same with only the computer: the short must not be fed for longer
 * than a few tries either. */
static void fault_on_computer(void)
{
    bench_t b;

    bench_init(&b, 15);
    b.host = b.configured = true;
    b.shorted = true;
    run(&b, 10000);
    CHECK(strcmp(power_state_name(&b.p), "locked") == 0);
    CHECK_EQ(b.p.fault_path, PWR_HOST);
    CHECK(b.on_ms[PWR_HOST] <= 3 * (PWR_FLAG_MS + 18 + 2));
    CHECK(!b.out.en_host);

    /* with both plugged in and a short, the hub does not hop from one to the other */
    bench_init(&b, 15);
    b.host = b.configured = true;
    b.gives[15] = true;
    b.charger = true;
    b.shorted = true;
    run(&b, 20000);
    CHECK(strcmp(power_state_name(&b.p), "locked") == 0);
    CHECK(b.on_ms[PWR_HOST] + b.on_ms[PWR_PD] <= 4 * 60);
}

/* A charger stepping down under a light load: the output stays above the
 * input for a while and the eFuse flags that. Not a fault. */
static void reverse_blocking(void)
{
    bench_t b;

    bench_init(&b, 15);
    b.gives[15] = b.gives[9] = true;
    b.charger = true;
    until_pd(&b, 5000);
    run(&b, 500);

    b.held_mv = 15000;
    power_set_pd(&b.p, 9, b.now);
    run(&b, 2000);
    CHECK_EQ(b.charger_mv, 9000);
    CHECK(b.out.en_pd);                         /* through the change and the flag */
    CHECK_EQ(b.p.tries, 0);
    CHECK_EQ(b.out.pd_volts, 9);
    b.held_mv = 0;
    run(&b, 100);
    CHECK_EQ(b.vin, 9000);
    CHECK(b.out.en_pd);

    /* and back up */
    power_set_pd(&b.p, 15, b.now);
    run(&b, 1000);
    CHECK_EQ(b.vin, 15000);
    CHECK(b.out.en_pd);
    CHECK_EQ(b.p.tries, 0);
}

static void console_commands(void)
{
    bench_t b;

    bench_init(&b, 15);
    b.host = b.configured = true;
    run(&b, 100);
    CHECK(b.out.en_host);
    power_command(&b.p, PWR_CMD_OFF, b.now);
    run(&b, 5000);
    CHECK(!b.out.en_host && !b.out.en_pd);
    CHECK(strcmp(power_state_name(&b.p), "manual off") == 0);
    power_command(&b.p, PWR_CMD_RETRY, b.now);  /* retry is not "on" */
    run(&b, 100);
    CHECK(!b.out.en_host);
    power_command(&b.p, PWR_CMD_AUTO, b.now);
    run(&b, 5);
    CHECK(b.out.en_host);
}

/* ---- the CH224A ---- */

typedef struct {
    bool powered;
    uint8_t addr;
    uint8_t status, amps;
    uint8_t volts_code;         /* what was last written, 0xFF: nothing */
    unsigned transfers, while_off;
    uint32_t last_at, min_gap;
    uint32_t *now;
} chip_t;

static bool chip_xfer(void *ctx, uint8_t addr, const uint8_t *w, size_t wn, uint8_t *r, size_t rn)
{
    chip_t *c = ctx;

    if (c->transfers && since(*c->now, c->last_at) < c->min_gap)
        c->min_gap = since(*c->now, c->last_at);
    c->last_at = *c->now;
    c->transfers++;
    if (!c->powered) {
        c->while_off++;
        return false;
    }
    if (addr != c->addr || wn < 1)
        return false;
    if (w[0] == CH224_REG_VOLTS && wn == 2 && rn == 0) {
        c->volts_code = w[1];
        return true;
    }
    if (wn == 1 && rn == 1 && w[0] == CH224_REG_STATUS) {
        r[0] = c->status;
        return true;
    }
    if (wn == 1 && rn == 1 && w[0] == CH224_REG_AMPS) {
        r[0] = c->amps;
        return true;
    }
    return false;
}

static void ch224(void)
{
    for (uint8_t addr = CH224_ADDR_A; addr <= CH224_ADDR_B; addr++) {
        uint32_t now = 100;
        chip_t chip = { .addr = addr, .status = CH224_ST_PD, .amps = 60, .volts_code = 0xFF,
                        .min_gap = 1000000, .now = &now };
        ch224_t c;

        ch224_init(&c, chip_xfer, &chip, now);

        /* no charger: not a single transfer */
        for (int i = 0; i < 3000; i++)
            ch224_poll(&c, false, 15, ++now);
        CHECK_EQ(chip.transfers, 0);

        /* charger in: found, told the voltage, read */
        chip.powered = true;
        for (int i = 0; i < 1000; i++)
            ch224_poll(&c, true, 15, ++now);
        CHECK_EQ(c.addr, addr);
        CHECK_EQ(chip.volts_code, 3);
        CHECK_EQ(c.asked, 15);
        CHECK_EQ(c.status, CH224_ST_PD);
        CHECK_EQ(c.max_ma, 3000);
        CHECK(chip.transfers <= 11);
        CHECK(chip.min_gap >= CH224_PERIOD_MS);

        /* another voltage: written once */
        unsigned before = chip.transfers;
        for (int i = 0; i < 1000; i++)
            ch224_poll(&c, true, 9, ++now);
        CHECK_EQ(chip.volts_code, 1);
        CHECK(chip.transfers - before <= 11);

        /* charger out and in again: the chip forgot, so it is told again */
        chip.powered = false;
        ch224_poll(&c, false, 9, ++now);
        chip.volts_code = 0xFF;
        CHECK_EQ(c.addr, 0);
        CHECK_EQ(c.max_ma, 0);
        chip.powered = true;
        unsigned at = chip.transfers;
        for (unsigned i = 0; i < CH224_START_MS - 1; i++)
            ch224_poll(&c, true, 9, ++now);
        CHECK_EQ(chip.transfers, at);           /* it gets time to wake up */
        for (int i = 0; i < 1000; i++)
            ch224_poll(&c, true, 9, ++now);
        CHECK_EQ(chip.volts_code, 1);
        CHECK_EQ(chip.while_off, 0);

        /* no PD: the current register is left alone */
        chip.status = CH224_ST_BC;
        chip.amps = 10;
        for (int i = 0; i < 1000; i++)
            ch224_poll(&c, true, 9, ++now);
        CHECK_EQ(c.status, CH224_ST_BC);
        CHECK_EQ(c.max_ma, 0);
    }

    CHECK_EQ(ch224_code(5), 0);
    CHECK_EQ(ch224_code(9), 1);
    CHECK_EQ(ch224_code(12), 2);
    CHECK_EQ(ch224_code(15), 3);
    CHECK_EQ(ch224_code(20), 4);
    CHECK_EQ(ch224_code(13), 0xFF);
}

/* A chip that never answers costs a transfer every period and nothing else. */
static void ch224_missing(void)
{
    uint32_t now = 0;
    chip_t chip = { .addr = 0x55, .powered = true, .min_gap = 1000000, .now = &now };
    ch224_t c;

    ch224_init(&c, chip_xfer, &chip, now);
    for (int i = 0; i < 5000; i++)
        ch224_poll(&c, true, 15, ++now);
    CHECK_EQ(c.addr, 0);
    CHECK(chip.transfers >= 45 && chip.transfers <= 50);
    CHECK(chip.min_gap >= CH224_PERIOD_MS);
}

int main(void)
{
    RUN(nothing_plugged);
    RUN(charger_15v);
    RUN(charger_lower);
    RUN(charger_stuck_at_5v);
    RUN(charger_too_high);
    RUN(computer_only);
    RUN(suspend);
    RUN(fault_and_retry);
    RUN(fault_on_computer);
    RUN(reverse_blocking);
    RUN(console_commands);
    RUN(ch224);
    RUN(ch224_missing);
    return T_DONE();
}
