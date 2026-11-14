/* The hub as a USB device: one serial port (CDC) and one MIDI port, on the
 * chip's full-speed controller (PA11, PA12), by TinyUSB.
 *
 * This file sets it up and holds what the computer asks about the device.
 * The data goes through TinyUSB's own calls (tud_cdc_..., tud_midi_...),
 * from hub/main.c. */
#ifndef PIANO_HUB_USB_H
#define PIANO_HUB_USB_H

#include <stdbool.h>
#include <stdint.h>

/* Start the device. Call after port_init(true). uid: the chip's ID, which
 * becomes the serial number the computer sees. */
void usb_start(const uint8_t uid[12]);

/* True once after the computer's program closed the serial port (it let
 * go of DTR, which it held). */
bool usb_serial_closed(void);

#endif
