/* The hub board's wiring, as the firmware needs it: a copy of the PINS table
 * and the constants under it in hub_circuit.py. If that file changes, this
 * one has to follow.
 *
 * Each line: the net, its MCU pin number, what the circuit says about it. */
#ifndef PIANO_HUB_BOARD_H
#define PIANO_HUB_BOARD_H

#include "port.h"

/* USB_DM (44, PA11), USB_DP (45, PA12): OTGFS1, also the ROM's USB DFU.
 * The USB controller takes these two by itself; nothing to set up. */

/* LINK_TX (16, PA2), LINK_RX (17, PA3): USART2, the cable to the tile. Pin A
 * of the cable is LINK_TX, pin B is LINK_RX, each through 220 ohm. The DMA
 * channels are ours to choose. */
#define LINK_HW { \
    USART2, CRM_USART2_PERIPH_CLOCK, GPIOA, GPIO_PINS_2, GPIO_PINS_3, \
    GPIO_PINS_SOURCE2, GPIO_PINS_SOURCE3, GPIO_MUX_7, \
    DMA1, DMA1_CHANNEL2, DMA1_CHANNEL3, DMA1MUX_CHANNEL2, DMA1MUX_CHANNEL3, \
    DMAMUX_DMAREQ_ID_USART2_RX, DMAMUX_DMAREQ_ID_USART2_TX }

/* AUX_TX (51, PC10), AUX_RX (52, PC11): USART3, a ROM bootloader UART. The
 * pads are not fitted and the firmware leaves the pins alone (analog). */

/* PD_SCL (58, PB6), PD_SDA (59, PB7): I2C1 to the CH224A, through 1k, with
 * that chip's own pull-ups only. port/at32f405/i2c.c has these two pins
 * built in. */

/* PD_PG (57, PB5): low = the charger agreed. Open drain, 100k to 3.3 V. */
#define PD_PG_GPIO      GPIOB
#define PD_PG_PIN       GPIO_PINS_5

/* EN_PD (30, PB12), EN_HOST (32, PB13): high = eFuse on. 100k to ground
 * each, so both are off until the firmware says otherwise. */
#define EN_GPIO         GPIOB
#define EN_PD_PIN       GPIO_PINS_12
#define EN_HOST_PIN     GPIO_PINS_13

/* ILIM_HI (28, PB2): open drain; low = 1.43 A from USB (puts R_ILM_HOST_HI
 * beside R_ILM_HOST), open = 0.5 A. */
#define ILIM_HI_GPIO    GPIOB
#define ILIM_HI_PIN     GPIO_PINS_2

/* FLT_PD (61, PB8), FLT_HOST (62, PB9): low = fault. Open drain, 100k to
 * 3.3 V each. */
#define FLT_GPIO        GPIOB
#define FLT_PD_PIN      GPIO_PINS_8
#define FLT_HOST_PIN    GPIO_PINS_9

/* The ADC inputs:
 *   VIN_SENSE   (8,  PC0)  ADC1_IN10   VIN through VIN_DIVIDER, 100n
 *   VPD_SENSE   (9,  PC1)  ADC1_IN11   V_PD through VIN_DIVIDER, 100n
 *   VBUS_SENSE  (10, PC2)  ADC1_IN12   VBUS through VBUS_DIVIDER, 100n
 *   IMON_PD     (11, PC3)  ADC1_IN13   the PD eFuse's ILM pin, through 10k
 *   IMON_HOST   (14, PA0)  ADC1_IN0    the USB eFuse's ILM pin, through 10k
 *   CC1_SENSE   (15, PA1)  ADC1_IN1    the USB socket's CC1, through 10k
 *   CC2_SENSE   (20, PA4)  ADC1_IN4    ... and CC2
 * The order here is the order of the ADC's sequence and of ADC_ enum below. */
#define ADC_GPIOC_PINS  (GPIO_PINS_0 | GPIO_PINS_1 | GPIO_PINS_2 | GPIO_PINS_3)
#define ADC_GPIOA_PINS  (GPIO_PINS_0 | GPIO_PINS_1 | GPIO_PINS_4)
#define ADC_CHANNELS \
    { ADC_CHANNEL_10, ADC_CHANNEL_11, ADC_CHANNEL_12, ADC_CHANNEL_13, \
      ADC_CHANNEL_0, ADC_CHANNEL_1, ADC_CHANNEL_4 }
enum { ADC_VIN, ADC_VPD, ADC_VBUS, ADC_IMON_PD, ADC_IMON_HOST, ADC_CC1, ADC_CC2, ADC_N };

/* LED_STATUS (37, PC6), LED_POWER (38, PC7): high = lit, through 470 ohm. */
#define LED_GPIO        GPIOC
#define LED_STATUS_PIN  GPIO_PINS_6
#define LED_POWER_PIN   GPIO_PINS_7

/* BTN_USER (2, PC13): low = pressed. 10k to 3.3 V on the board. */
#define BTN_USER_GPIO   GPIOC
#define BTN_USER_PIN    GPIO_PINS_13

/* SWDIO (46, PA13), SWCLK (49, PA14): the debugger's; left as they are.
 * BOOT0 (60, PF11/BOOT0): 3.3k to ground, and the BOOT button to 3.3 V. */

/* What the ADC sees. */
#define VIN_DIVIDER_TOP     100000u     /* VIN_DIVIDER = (100e3, 15e3): VIN and V_PD, top, bottom */
#define VIN_DIVIDER_BOTTOM  15000u
#define VBUS_DIVIDER_TOP    100000u     /* VBUS_DIVIDER = (100e3, 100e3) */
#define VBUS_DIVIDER_BOTTOM 100000u
#define IMON_GAIN_UA_PER_A  182u        /* IMON_GAIN = 182e-6 A per A out of the eFuse's ILM pin */
#define R_ILM_PD            1650u       /* ohm: limit 3334 / R = 2.02 A */
#define R_ILM_HOST          6650u       /* ohm: 0.50 A */
#define R_ILM_HOST_HI       3600u       /* ohm, in parallel when ILIM_HI is low: 1.43 A */

#endif
