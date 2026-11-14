/* The AT32F405RCT7 itself: clock, time, the chip's ID, restarting. Shared
 * by the tile and the hub.
 *
 * Nothing in this directory has run on a chip yet. Every register-level
 * fact was checked against Artery's headers, examples and reference manual
 * (RM_AT32F402_405 v2.02), except where a comment says "not verified". */
#ifndef PIANO_PORT_H
#define PIANO_PORT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "at32f402_405.h"

#define PORT_HCLK_HZ   216000000u
#define PORT_APB1_HZ   108000000u      /* USART2, USART3, UART8, I2C1 */
#define PORT_APB2_HZ   216000000u      /* USART6, ADC1 */
#define PORT_TMR_HZ    216000000u      /* every timer: the APB1 ones count at twice APB1 */

/* The first line of main(): goes to the ROM bootloader, and does not come
 * back, if port_bootloader() asked for that before the reset. */
void port_boot_check(void);

/* 216 MHz from the 12 MHz crystal, a millisecond tick, every pin analog
 * except the two debug pins. usb: also start the 48 MHz USB clock and the
 * USB controller's clock. */
void port_init(bool usb);

/* Milliseconds since port_init(). Keeps time across the moments the chip
 * stands still (while flash is erased, see flash.c). */
uint32_t port_ms(void);

/* The chip's 96-bit ID. */
void port_uid(uint8_t uid[12]);

void port_reboot(void);

/* Restart into the ROM bootloader (UART on USART1/2/3, or USB DFU). */
void port_bootloader(void);

/* Set some pins of one GPIO port up. */
void port_pins(gpio_type *gpio, uint16_t pins, gpio_mode_type mode, gpio_output_type type,
               gpio_pull_type pull);

#endif
