/* I2C1 as a slow, polled master. See i2c.h.
 *
 * The order of the steps is that of Artery's i2c_application library
 * (i2c_master_transmit / i2c_memory_read). */
#include "i2c.h"

/* The clock word for 10 kHz with PCLK1 at 108 MHz, from Artery's examples
 * ("0x4170FEFE // 10K", same clock tree): the divider is 0x14 + 1 = 21 and
 * SCL is high for 0xFE + 1 and low for 0xFE + 1 of those, so one bit is
 * 510 * 21 / 108 MHz = 99 us. */
#define I2C_CLKCTRL     0x4170FEFEu
#define I2C_FILTER      0x0F        /* the longest digital filter on both lines */
#define I2C_TIMEOUT_MS  20u         /* a whole transfer; the longest one takes 4 ms */

static void (*wait_hook)(void);

static void setup(void)
{
    i2c_reset(I2C1);
    i2c_init(I2C1, I2C_FILTER, I2C_CLKCTRL);
    i2c_enable(I2C1, TRUE);
}

void i2c1_start(void (*wait)(void))
{
    wait_hook = wait;
    crm_periph_clock_enable(CRM_I2C1_PERIPH_CLOCK, TRUE);

    /* Open drain, and no pull-up of ours: the CH224A has its own, and a
     * pull-up here would feed that chip through its pins while the
     * charger is unplugged. */
    gpio_pin_mux_config(GPIOB, GPIO_PINS_SOURCE6, GPIO_MUX_4);
    gpio_pin_mux_config(GPIOB, GPIO_PINS_SOURCE7, GPIO_MUX_4);
    port_pins(GPIOB, GPIO_PINS_6 | GPIO_PINS_7, GPIO_MODE_MUX, GPIO_OUTPUT_OPEN_DRAIN,
              GPIO_PULL_NONE);
    setup();
}

/* Wait for a flag. False if the other end did not acknowledge, or time ran out. */
static bool wait_for(uint32_t flag, uint32_t deadline)
{
    while (i2c_flag_get(I2C1, flag) == RESET) {
        if (i2c_flag_get(I2C1, I2C_ACKFAIL_FLAG) != RESET)
            return false;
        if ((int32_t)(port_ms() - deadline) >= 0)
            return false;
        if (wait_hook)
            wait_hook();
    }
    return true;
}

bool i2c1_xfer(void *ctx, uint8_t addr, const uint8_t *w, size_t wn, uint8_t *r, size_t rn)
{
    uint32_t deadline = port_ms() + I2C_TIMEOUT_MS;
    uint16_t addr8 = (uint16_t)(addr << 1);
    bool ok = true;

    (void)ctx;
    if (wn > 255 || rn > 255 || (wn == 0 && rn == 0))
        return false;

    /* The lines sat low while the CH224A had no power, and may have come up
     * in an order that looks like a transfer somebody else started. */
    if (i2c_flag_get(I2C1, I2C_BUSYF_FLAG) != RESET) {
        setup();
        if (i2c_flag_get(I2C1, I2C_BUSYF_FLAG) != RESET)
            return false;
    }

    if (wn) {
        i2c_transmit_set(I2C1, addr8, (uint8_t)wn, rn ? I2C_SOFT_STOP_MODE : I2C_AUTO_STOP_MODE,
                         I2C_GEN_START_WRITE);
        for (size_t i = 0; ok && i < wn; i++) {
            ok = wait_for(I2C_TDIS_FLAG, deadline);
            if (ok)
                i2c_data_send(I2C1, w[i]);
        }
        if (ok && rn)
            ok = wait_for(I2C_TDC_FLAG, deadline);
    }
    if (ok && rn) {
        i2c_transmit_set(I2C1, addr8, (uint8_t)rn, I2C_AUTO_STOP_MODE, I2C_GEN_START_READ);
        for (size_t i = 0; ok && i < rn; i++) {
            ok = wait_for(I2C_RDBF_FLAG, deadline);
            if (ok)
                r[i] = i2c_data_receive(I2C1);
        }
    }
    if (ok)
        ok = wait_for(I2C_STOPF_FLAG, deadline);

    if (ok || i2c_flag_get(I2C1, I2C_ACKFAIL_FLAG) != RESET) {
        /* Done, or not acknowledged: then the chip sends the stop itself.
         * Clear the flags and the transfer's settings, as Artery's code does. */
        if (!ok) {
            uint32_t stop_by = port_ms() + 2;

            while (i2c_flag_get(I2C1, I2C_STOPF_FLAG) == RESET &&
                   (int32_t)(port_ms() - stop_by) < 0) {
            }
        }
        i2c_flag_clear(I2C1, I2C_STOPF_FLAG | I2C_ACKFAIL_FLAG);
        I2C1->ctrl2_bit.saddr = 0;
        I2C1->ctrl2_bit.readh10 = 0;
        I2C1->ctrl2_bit.cnt = 0;
        I2C1->ctrl2_bit.rlden = 0;
        I2C1->ctrl2_bit.dir = 0;
    } else {
        /* Stuck half way: start the peripheral afresh. */
        setup();
    }
    return ok;
}
