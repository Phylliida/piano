/* What TinyUSB is asked to be on the hub: a full-speed device on the chip's
 * first USB controller (OTGFS1, TinyUSB's port 0, pins PA11 and PA12) with
 * one serial port (CDC) and one MIDI port.
 *
 * TinyUSB includes this file by name. The Makefile gives it the chip:
 * -DCFG_TUSB_MCU=OPT_MCU_AT32F402_405. */
#ifndef PIANO_TUSB_CONFIG_H
#define PIANO_TUSB_CONFIG_H

#define CFG_TUSB_OS             OPT_OS_NONE
#define CFG_TUSB_DEBUG          0

#define CFG_TUD_ENABLED         1
#define CFG_TUD_MAX_SPEED       OPT_MODE_FULL_SPEED
#define CFG_TUD_ENDPOINT0_SIZE  64

#define CFG_TUSB_MEM_ALIGN      __attribute__((aligned(4)))

#define CFG_TUD_CDC             1
#define CFG_TUD_MIDI            1
#define CFG_TUD_MSC             0
#define CFG_TUD_HID             0
#define CFG_TUD_VENDOR          0

/* The serial port. Packets are 64 bytes (the most full speed allows); the
 * buffers behind them hold a few packets. */
#define CFG_TUD_CDC_RX_BUFSIZE  256
#define CFG_TUD_CDC_TX_BUFSIZE  256
#define CFG_TUD_CDC_EP_BUFSIZE  64

/* MIDI: a message is 4 bytes on USB, so 256 bytes are 64 messages. TinyUSB
 * has no default for these two. */
#define CFG_TUD_MIDI_RX_BUFSIZE 64
#define CFG_TUD_MIDI_TX_BUFSIZE 256
#define CFG_TUD_MIDI_EP_BUFSIZE 64

#endif
