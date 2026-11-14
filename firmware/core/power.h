/* The hub's power policy: which of its two inputs feeds the tiles.
 * DESIGN.md, "Power".
 *
 * One state machine, fed about once a millisecond with what the board
 * measures and answering with what its pins should do. It touches no
 * hardware, so the tests can plug chargers in and short the output. */
#ifndef PIANO_POWER_H
#define PIANO_POWER_H

#include "util.h"

#define PWR_PRESENT_MV    4000u   /* above this an input is plugged in */
#define PWR_PD_GOOD_MV    8000u   /* above this the charger gave more than 5 V */
#define PWR_PD_MAX_MV     17000u  /* the board itself cuts off at 17.8 V */
#define PWR_PD_5V_MAX_MV  6500u
#define PWR_PD_STEP_MS    1500u   /* how long the charger gets to give what was asked */
#define PWR_PD_STABLE_MS  50u     /* ... and how long it has to hold it */
#define PWR_GRACE_MS      100u    /* VIN has this long to come up after switching on */
#define PWR_LOW_MS        10u     /* VIN has to be low this long to count */
#define PWR_FLAG_MS       20u     /* and a fault flag this long */
#define PWR_OVERLAP_MS    20u     /* both paths on while the charger takes over */
#define PWR_RETRY_MS      1000u   /* off this long after a fault */
#define PWR_TRIES         3       /* faults in a row before staying off */
#define PWR_HEALTHY_MS    10000u  /* on this long: the faults before are forgotten */
#define PWR_CC_1A5_MV     660u    /* the computer offers 1.5 A above this (Type-C table 4-36) */
#define PWR_CC_3A_MV      1230u   /* and 3 A above this */
#define PWR_CC_STABLE_MS  100u

typedef struct {
    uint16_t vin_mv;            /* the output to the tiles */
    uint16_t vpd_mv;            /* the charger's socket, after the CH224A */
    uint16_t vhost_mv;          /* the computer's socket */
    uint16_t ipd_ma, ihost_ma;  /* what each path carries; shown, not acted on */
    uint16_t cc1_mv, cc2_mv;    /* the computer's socket's CC pins */
    bool pd_good;               /* the CH224A says the charger agreed; shown, not acted on */
    bool flt_pd, flt_host;      /* an eFuse's fault flag is up */
    bool usb_configured;        /* the computer has set the device up */
    bool usb_suspended;
} power_in_t;

typedef struct {
    bool en_pd, en_host;        /* the two eFuses */
    bool ilim_hi;               /* let the computer's path carry 1.43 A instead of 0.5 A */
    uint8_t pd_volts;           /* what to ask the charger for */
} power_out_t;

enum { PWR_NONE, PWR_HOST, PWR_PD };                        /* path */
enum { PWR_GO, PWR_RETRY, PWR_LOCKED, PWR_MANUAL };         /* hold */
enum { PWR_OK, PWR_FAULT_FLAG, PWR_FAULT_LOW };             /* fault */
enum { PWR_CMD_AUTO, PWR_CMD_OFF, PWR_CMD_RETRY };

typedef struct {
    uint8_t path;               /* which eFuse is on */
    uint8_t hold;               /* why none may be */
    uint8_t tries;              /* faults in a row */
    uint8_t fault, fault_path;  /* the last fault and where it was */
    bool overlap;               /* the computer's path is still on while the charger's comes up */
    bool ilim_hi;

    uint8_t pd_set;             /* the voltage the settings want */
    uint8_t pd_ask;             /* the voltage being asked for now */
    bool pd_asking;             /* waiting for the charger to give it */
    bool pd_ready;              /* the charger's voltage is the one to use */
    bool pd_was, host_was;      /* each input was plugged in at the last step */

    uint32_t pd_since;          /* when pd_ask was first asked for */
    uint32_t pd_good_since;     /* since when the charger has given it */
    uint32_t on_since;          /* when `path` last changed */
    uint32_t flag_since, low_since;
    uint32_t overlap_until, hold_until;
    uint32_t cc_since;          /* since when the computer has offered more than 0.5 A */

    power_in_t in;              /* the last measurements, for the console */
} power_t;

/* pd_volts: 5, 9, 12 or 15. Everything starts off. */
void power_init(power_t *p, uint8_t pd_volts, uint32_t now);

void power_step(power_t *p, const power_in_t *in, uint32_t now, power_out_t *out);

/* From the console: PWR_CMD_OFF keeps everything off until PWR_CMD_AUTO;
 * PWR_CMD_RETRY tries again now after faults. */
void power_command(power_t *p, uint8_t cmd, uint32_t now);

/* Ask the charger for another voltage. */
void power_set_pd(power_t *p, uint8_t volts, uint32_t now);

/* "pd", "host", "off", "pd wait", "retry", "locked", "manual off". */
const char *power_state_name(const power_t *p);

/* What the computer's CC pins offer: 500, 1500 or 3000 mA. */
uint16_t power_host_offer_ma(const power_t *p);

#endif
