/* The CH224A, the chip that talks USB Power Delivery to the charger. Its
 * resistor already asks for 15 V; over I2C the hub can ask for another
 * voltage and read what the charger offers.
 *
 * The chip is powered by the charger and its I2C lines have only the chip's
 * own pull-ups (behind 1k), so: talk slowly (20 kHz or less, the port's
 * job) and only while the charger is plugged in (ch224_poll's `powered`). */
#ifndef PIANO_CH224_H
#define PIANO_CH224_H

#include "util.h"

#define CH224_ADDR_A      0x22u   /* the chip answers at one of these two */
#define CH224_ADDR_B      0x23u
#define CH224_REG_STATUS  0x09u   /* read: which protocol is active */
#define CH224_REG_VOLTS   0x0Au   /* write: 0 = 5 V, 1 = 9, 2 = 12, 3 = 15, 4 = 20 */
#define CH224_REG_AMPS    0x50u   /* read: what the charger offers, in units of 50 mA */

#define CH224_ST_BC       0x01u
#define CH224_ST_QC2      0x02u
#define CH224_ST_QC3      0x04u
#define CH224_ST_PD       0x08u
#define CH224_ST_EPR      0x10u

#define CH224_START_MS    100u    /* after power, before the first word */
#define CH224_PERIOD_MS   100u    /* between transfers */

/* One I2C transfer: write `wn` bytes to the 7-bit address, then (if rn)
 * a repeated start and read `rn` bytes. True if every byte was acknowledged. */
typedef bool (*ch224_xfer_fn)(void *ctx, uint8_t addr, const uint8_t *w, size_t wn,
                              uint8_t *r, size_t rn);

typedef struct {
    ch224_xfer_fn xfer;
    void *ctx;
    uint8_t addr;               /* 0: not found (yet) */
    uint8_t asked;              /* the voltage last written, 0: none */
    uint8_t status;             /* CH224_ST_ bits */
    uint16_t max_ma;            /* the charger's offer at this voltage, 0: not known */
    uint8_t turn;               /* which transfer is next */
    uint16_t errors;            /* transfers that failed */
    uint32_t next;              /* when the next transfer may go */
} ch224_t;

void ch224_init(ch224_t *c, ch224_xfer_fn xfer, void *ctx, uint32_t now);

/* Call about once a millisecond. Does one transfer at most, and none while
 * `powered` is false. `volts` (5, 9, 12, 15 or 20) is written when it
 * differs from what the chip was last told. */
void ch224_poll(ch224_t *c, bool powered, uint8_t volts, uint32_t now);

/* The register value for a voltage, or 0xFF. */
uint8_t ch224_code(uint8_t volts);

#endif
