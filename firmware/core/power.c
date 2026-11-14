#include "power.h"

#include <string.h>

void power_init(power_t *p, uint8_t pd_volts, uint32_t now)
{
    memset(p, 0, sizeof *p);
    p->pd_set = pd_volts;
    p->pd_ask = pd_volts;
    p->pd_since = p->pd_good_since = p->on_since = now;
    p->flag_since = p->low_since = p->cc_since = now;
}

static void start_asking(power_t *p, uint32_t now)
{
    p->pd_asking = true;
    p->pd_ask = p->pd_set;
    p->pd_since = now;
    p->pd_good_since = now;
}

/* The charger: ask for the voltage the settings want; if it stays at 5 V,
 * ask for less; if nothing helps, take what there is. */
static void step_charger(power_t *p, bool here, uint16_t mv, uint32_t now)
{
    if (!here) {
        p->pd_asking = false;
        p->pd_ready = false;
        p->pd_ask = p->pd_set;
        return;
    }
    if (!p->pd_was)
        start_asking(p, now);

    bool good = p->pd_ask == 5 ? mv <= PWR_PD_5V_MAX_MV
                               : mv >= PWR_PD_GOOD_MV && mv <= PWR_PD_MAX_MV;
    if (!good)
        p->pd_good_since = now;
    if (!p->pd_asking)
        return;

    if (good && since(now, p->pd_good_since) >= PWR_PD_STABLE_MS) {
        p->pd_asking = false;
        p->pd_ready = true;
    } else if (since(now, p->pd_since) >= PWR_PD_STEP_MS) {
        uint8_t next = p->pd_ask > 12 ? 12 : p->pd_ask > 9 ? 9 : 0;
        if (next) {
            p->pd_ask = next;
            p->pd_since = now;
        } else {
            p->pd_asking = false;       /* as it is, then */
            p->pd_ready = true;
        }
    }
}

/* Is the path that is on in trouble? */
static uint8_t check_fault(power_t *p, const power_in_t *in, bool here, uint32_t now)
{
    uint32_t src = p->path == PWR_PD ? in->vpd_mv : in->vhost_mv;
    bool flag = p->path == PWR_PD ? in->flt_pd : in->flt_host;
    uint32_t vin = in->vin_mv;
    uint8_t fault = PWR_OK;

    if (!here) {
        /* the input was pulled out: nothing wrong with that */
        p->flag_since = p->low_since = now;
        return PWR_OK;
    }

    /* An eFuse also raises its flag while it blocks reverse current, which
     * happens whenever a charger steps its voltage down under a light load.
     * Then the output is above the input. In a real fault it is below. */
    if (!(flag && vin * 100u < src * 95u))
        p->flag_since = now;
    else if (since(now, p->flag_since) >= PWR_FLAG_MS)
        fault = PWR_FAULT_FLAG;

    if (!(vin * 5u < src * 4u) || since(now, p->on_since) < PWR_GRACE_MS)
        p->low_since = now;
    else if (since(now, p->low_since) >= PWR_LOW_MS)
        fault = PWR_FAULT_LOW;

    return fault;
}

void power_step(power_t *p, const power_in_t *in, uint32_t now, power_out_t *out)
{
    bool pd_here = in->vpd_mv >= PWR_PRESENT_MV;
    bool host_here = in->vhost_mv >= PWR_PRESENT_MV;

    p->in = *in;

    /* An input pulled out: what was wrong may have gone with it. */
    if ((p->pd_was && !pd_here) || (p->host_was && !host_here)) {
        if (p->hold == PWR_RETRY || p->hold == PWR_LOCKED)
            p->hold = PWR_GO;
        p->tries = 0;
    }

    step_charger(p, pd_here, in->vpd_mv, now);

    if (p->path != PWR_NONE) {
        uint8_t fault = check_fault(p, in, p->path == PWR_PD ? pd_here : host_here, now);
        if (fault != PWR_OK) {
            p->fault = fault;
            p->fault_path = p->path;
            p->path = PWR_NONE;
            p->on_since = now;
            if (++p->tries >= PWR_TRIES) {
                p->hold = PWR_LOCKED;
            } else {
                p->hold = PWR_RETRY;
                p->hold_until = now + PWR_RETRY_MS;
            }
        } else if (since(now, p->on_since) >= PWR_HEALTHY_MS) {
            p->tries = 0;
        }
    }
    if (p->hold == PWR_RETRY && reached(now, p->hold_until))
        p->hold = PWR_GO;

    /* Which path should be on: the charger's if it is ready, else the
     * computer's if the computer allows it. */
    uint8_t want = PWR_NONE;
    if (p->hold == PWR_GO) {
        if (p->pd_ready && in->vpd_mv <= PWR_PD_MAX_MV)
            want = PWR_PD;
        else if (host_here && in->usb_configured && !in->usb_suspended)
            want = PWR_HOST;
    }
    if (want != p->path) {
        /* from the computer to the charger: both on for a moment, so the
         * tiles never see the power go (the eFuses block reverse current) */
        p->overlap = p->path == PWR_HOST && want == PWR_PD;
        p->overlap_until = now + PWR_OVERLAP_MS;
        p->path = want;
        p->on_since = p->flag_since = p->low_since = now;
    }
    if (p->overlap && (p->path != PWR_PD || reached(now, p->overlap_until)))
        p->overlap = false;

    /* The computer's current limit: raised only after its offer has been
     * steady, lowered at once. */
    uint16_t cc = in->cc1_mv > in->cc2_mv ? in->cc1_mv : in->cc2_mv;
    if (cc < PWR_CC_1A5_MV) {
        p->ilim_hi = false;
        p->cc_since = now;
    } else if (since(now, p->cc_since) >= PWR_CC_STABLE_MS) {
        p->ilim_hi = true;
    }

    p->pd_was = pd_here;
    p->host_was = host_here;

    out->en_pd = p->path == PWR_PD;
    out->en_host = p->path == PWR_HOST || p->overlap;
    out->ilim_hi = p->ilim_hi;
    out->pd_volts = p->pd_ask;
}

void power_command(power_t *p, uint8_t cmd, uint32_t now)
{
    switch (cmd) {
    case PWR_CMD_OFF:
        p->hold = PWR_MANUAL;
        p->path = PWR_NONE;
        p->overlap = false;
        p->on_since = now;
        break;
    case PWR_CMD_AUTO:
        p->hold = PWR_GO;
        p->tries = 0;
        break;
    case PWR_CMD_RETRY:
        if (p->hold == PWR_RETRY || p->hold == PWR_LOCKED) {
            p->hold = PWR_GO;
            p->tries = 0;
        }
        break;
    default:
        break;
    }
}

void power_set_pd(power_t *p, uint8_t volts, uint32_t now)
{
    p->pd_set = volts;
    p->pd_ask = volts;
    if (p->pd_was)
        start_asking(p, now);   /* pd_ready stays: a path that is on stays on */
}

const char *power_state_name(const power_t *p)
{
    if (p->hold == PWR_MANUAL)
        return "manual off";
    if (p->hold == PWR_LOCKED)
        return "locked";
    if (p->hold == PWR_RETRY)
        return "retry";
    if (p->path == PWR_PD)
        return "pd";
    if (p->path == PWR_HOST)
        return "host";
    return p->pd_asking ? "pd wait" : "off";
}

uint16_t power_host_offer_ma(const power_t *p)
{
    uint16_t cc = p->in.cc1_mv > p->in.cc2_mv ? p->in.cc1_mv : p->in.cc2_mv;

    return cc >= PWR_CC_3A_MV ? 3000 : cc >= PWR_CC_1A5_MV ? 1500 : 500;
}
