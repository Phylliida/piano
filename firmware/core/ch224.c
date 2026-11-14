#include "ch224.h"

void ch224_init(ch224_t *c, ch224_xfer_fn xfer, void *ctx, uint32_t now)
{
    c->xfer = xfer;
    c->ctx = ctx;
    c->addr = 0;
    c->asked = 0;
    c->status = 0;
    c->max_ma = 0;
    c->turn = 0;
    c->errors = 0;
    c->next = now + CH224_START_MS;
}

uint8_t ch224_code(uint8_t volts)
{
    switch (volts) {
    case 5:  return 0;
    case 9:  return 1;
    case 12: return 2;
    case 15: return 3;
    case 20: return 4;
    default: return 0xFF;
    }
}

static bool read_reg(ch224_t *c, uint8_t addr, uint8_t reg, uint8_t *value)
{
    return c->xfer(c->ctx, addr, &reg, 1, value, 1);
}

void ch224_poll(ch224_t *c, bool powered, uint8_t volts, uint32_t now)
{
    uint8_t v;

    if (!powered) {
        /* the chip is off: it has forgotten what it was told */
        c->addr = 0;
        c->asked = 0;
        c->status = 0;
        c->max_ma = 0;
        c->next = now + CH224_START_MS;
        return;
    }
    if (!reached(now, c->next))
        return;
    c->next = now + CH224_PERIOD_MS;
    c->turn++;

    if (c->addr == 0) {
        /* look for it at one address, then at the other */
        uint8_t addr = (c->turn & 1u) ? CH224_ADDR_A : CH224_ADDR_B;
        if (read_reg(c, addr, CH224_REG_STATUS, &v)) {
            c->addr = addr;
            c->status = v;
        }
        return;
    }

    if (c->asked != volts && ch224_code(volts) != 0xFF) {
        uint8_t w[2] = { CH224_REG_VOLTS, ch224_code(volts) };
        if (c->xfer(c->ctx, c->addr, w, 2, NULL, 0)) {
            c->asked = volts;
            c->max_ma = 0;
        } else {
            c->errors++;
        }
        return;
    }

    if (c->turn & 1u) {
        if (read_reg(c, c->addr, CH224_REG_STATUS, &v)) {
            c->status = v;
            if (!(v & CH224_ST_PD))
                c->max_ma = 0;          /* an offer from before means nothing now */
        } else {
            c->errors++;
        }
    } else if (c->status & CH224_ST_PD) {
        /* only means something once the charger and the chip have agreed */
        if (read_reg(c, c->addr, CH224_REG_AMPS, &v))
            c->max_ma = (uint16_t)(v * 50u);
        else
            c->errors++;
    }
}
