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
#include "fw.h"
#include "proto.h"

#define PORT_HCLK_HZ   216000000u
#define PORT_APB1_HZ   108000000u      /* USART2, USART3, UART8, I2C1 */
#define PORT_APB2_HZ   216000000u      /* USART6, ADC1 */
#define PORT_TMR_HZ    216000000u      /* every timer: the APB1 ones count at twice APB1 */

/* ---- the program as an image (core/fw.h) ----
 *
 * The chip starts in our bootloader (boot/), which starts the program in
 * slot A or slot B. A program is linked for one of the two, and the linker
 * script says where it begins. */
extern const uint8_t _image_start[];

/* The slot this program runs from: FW_SLOT_A or FW_SLOT_B. */
uint8_t port_slot(void);

/* The image's header, which the linker script puts 0x200 bytes into the
 * program. Its length stays 0 here: tools/mkimage.py writes it, and the
 * checksum behind the program. */
struct port_image_header {
    uint32_t magic;
    uint8_t kind, zero;
    uint16_t version;
    const uint8_t *base;
    uint32_t length;
};
_Static_assert(sizeof(struct port_image_header) == FW_HEADER_LEN, "the image header's layout");

/* Once in a program, beside main(): PORT_IMAGE_HEADER(KIND_TILE); */
#define PORT_IMAGE_HEADER(kind_) \
    const struct port_image_header port_image_header \
        __attribute__((section(".fw_header"), used)) = \
        { FW_IMAGE_MAGIC, (kind_), 0, FW_VERSION, _image_start, 0 }

/* One word of RAM that no program keeps a variable in (the linker scripts
 * leave it out) and that lives through a reset: what a program wants from
 * the bootloader at the next start. */
#define PORT_MAILBOX      (*(volatile uint32_t *)0x20000000u)
#define PORT_MAILBOX_ROM  0xB007AB1Eu   /* go on into the ROM bootloader */

/* The first call in main(). 216 MHz from the 12 MHz crystal, a millisecond
 * tick, every pin analog except the two debug pins. usb: also start the
 * 48 MHz USB clock and the USB controller's clock. */
void port_init(bool usb);

/* Every time round the main loop. The bootloader starts the watchdog before
 * a new program's first run (about 4 s): a program that hangs is restarted,
 * and is not started a second time. Without the watchdog this does nothing. */
void port_alive(void);

/* Milliseconds since port_init(). Keeps time across the moments the chip
 * stands still (while flash is erased, see flash.c). */
uint32_t port_ms(void);

/* The chip's 96-bit ID. */
void port_uid(uint8_t uid[12]);

void port_reboot(void);

/* Restart into the ROM bootloader (UART on USART1/2/3, or USB DFU): by way
 * of our bootloader, which finds the word left in PORT_MAILBOX. */
void port_bootloader(void);

/* Set some pins of one GPIO port up. */
void port_pins(gpio_type *gpio, uint16_t pins, gpio_mode_type mode, gpio_output_type type,
               gpio_pull_type pull);

#endif
