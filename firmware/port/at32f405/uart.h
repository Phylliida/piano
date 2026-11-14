/* One serial port of a tile or the hub: a UART whose two pins can change
 * places (DESIGN.md, "Finding the orientation").
 *
 * Received bytes are put into a ring by DMA, with no interrupt and no
 * program step in between, so nothing is lost while the program is busy
 * or standing still (a flash erase). Frames are sent by DMA too. */
#ifndef PIANO_UART_H
#define PIANO_UART_H

#include "port.h"

/* 20 ms of a cable carrying bytes back to back at 1 Mbaud is 2000 bytes:
 * that is how long a flash erase can keep the program from looking. */
#define UART_RX_LEN  4096
#define UART_TX_LEN  128        /* one frame; the bridge sends at most this at a time */

/* Which UART, which pins, which DMA channels. */
typedef struct {
    usart_type *usart;
    crm_periph_clock_type clock;
    gpio_type *gpio;                        /* both pins are on this port */
    uint16_t pin_a, pin_b;                  /* the cable's pin A (sends, unswapped) and pin B */
    gpio_pins_source_type src_a, src_b;
    gpio_mux_sel_type mux;
    dma_type *dma;                          /* both channels belong to this controller */
    dma_channel_type *rx_ch, *tx_ch;
    dmamux_channel_type *rx_mux, *tx_mux;
    dmamux_requst_id_sel_type rx_req, tx_req;
} uart_hw_t;

typedef struct {
    const uart_hw_t *hw;
    uint16_t rx_at;             /* the next byte of the ring to hand out */
    bool tx_busy;
    bool swapped;
    uint8_t rx[UART_RX_LEN];
    uint8_t tx[UART_TX_LEN];
} uart_t;

/* 1 Mbaud, 8N1, pins not swapped, pull-ups on both pins. */
void uart_init(uart_t *u, const uart_hw_t *hw);

/* Change speed, parity ('n', 'e' or 'o') and pins: for the hub's bridge.
 * Call only while uart_tx_idle(). What was received and not read is dropped. */
void uart_setup(uart_t *u, uint32_t baud, char parity, bool swapped);

/* Let the two pins change places. Call only while uart_tx_idle(). */
void uart_swap(uart_t *u, bool swapped);

/* Up to `max` received bytes; returns how many. */
size_t uart_read(uart_t *u, uint8_t *buf, size_t max);

/* True when the last byte given to uart_write() has left the pin. */
bool uart_tx_idle(uart_t *u);

/* Send `len` bytes (at most UART_TX_LEN). Call only while uart_tx_idle(). */
void uart_write(uart_t *u, const uint8_t *data, size_t len);

#endif
