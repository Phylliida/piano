/* The tile board's wiring, as the firmware needs it: a copy of the tables
 * in tile_circuit.py (SELECT_PINS, ADC_PINS, ADC_CHANNEL, CHANNEL_OF_COL,
 * UARTS). If that file changes, this one has to follow. */
#ifndef PIANO_TILE_BOARD_H
#define PIANO_TILE_BOARD_H

#include "port.h"

/* SELECT_PINS: the three select lines every mux shares.
 * MUX_S0 = PB0, MUX_S1 = PC5, MUX_S2 = PC4. */
#define SEL0_GPIO   GPIOB
#define SEL0_PIN    GPIO_PINS_0
#define SEL1_GPIO   GPIOC
#define SEL1_PIN    GPIO_PINS_5
#define SEL2_GPIO   GPIOC
#define SEL2_PIN    GPIO_PINS_4

/* ADC_PINS and ADC_CHANNEL: mux m reads key row m, and its output
 * MUX_OUT1..5 is on PA4, PA5, PA6, PB1, PA7 = ADC1_IN4, IN5, IN6, IN9, IN7. */
#define ADC_GPIOA_PINS  (GPIO_PINS_4 | GPIO_PINS_5 | GPIO_PINS_6 | GPIO_PINS_7)
#define ADC_GPIOB_PINS  GPIO_PINS_1
#define ADC_CHANNEL_OF_ROW \
    { ADC_CHANNEL_4, ADC_CHANNEL_5, ADC_CHANNEL_6, ADC_CHANNEL_9, ADC_CHANNEL_7 }

/* CHANNEL_OF_COL[row][column]: the key in (column c, row m) is read at
 * select value CHANNEL_OF_COL[m][c]. Row 3's mux is turned a quarter turn
 * on the board and has an order of its own. */
#define CHANNEL_OF_COL \
    { { 6, 7, 4, 5, 2, 3, 1, 0 }, \
      { 6, 7, 4, 5, 2, 3, 1, 0 }, \
      { 6, 7, 4, 5, 2, 3, 1, 0 }, \
      { 0, 1, 3, 2, 5, 4, 7, 6 }, \
      { 6, 7, 4, 5, 2, 3, 1, 0 } }

/* UARTS: one per edge, (TX, RX) = the cable's pins (A, B) through 220 ohm.
 *   W  USART2  PA2,  PA3     (also a ROM bootloader UART)
 *   N  USART6  PC6,  PC7
 *   E  UART8   PC8,  PC9
 *   S  USART3  PC10, PC11    (also a ROM bootloader UART)
 * The IOMUX numbers are from the reference manual's chapter 6; the DMA
 * channels are ours to choose (any request can go to any channel). */
#define UART_W_HW { \
    USART2, CRM_USART2_PERIPH_CLOCK, GPIOA, GPIO_PINS_2, GPIO_PINS_3, \
    GPIO_PINS_SOURCE2, GPIO_PINS_SOURCE3, GPIO_MUX_7, \
    DMA1, DMA1_CHANNEL2, DMA1_CHANNEL3, DMA1MUX_CHANNEL2, DMA1MUX_CHANNEL3, \
    DMAMUX_DMAREQ_ID_USART2_RX, DMAMUX_DMAREQ_ID_USART2_TX }
#define UART_N_HW { \
    USART6, CRM_USART6_PERIPH_CLOCK, GPIOC, GPIO_PINS_6, GPIO_PINS_7, \
    GPIO_PINS_SOURCE6, GPIO_PINS_SOURCE7, GPIO_MUX_8, \
    DMA1, DMA1_CHANNEL4, DMA1_CHANNEL5, DMA1MUX_CHANNEL4, DMA1MUX_CHANNEL5, \
    DMAMUX_DMAREQ_ID_USART6_RX, DMAMUX_DMAREQ_ID_USART6_TX }
#define UART_E_HW { \
    UART8, CRM_UART8_PERIPH_CLOCK, GPIOC, GPIO_PINS_8, GPIO_PINS_9, \
    GPIO_PINS_SOURCE8, GPIO_PINS_SOURCE9, GPIO_MUX_7, \
    DMA1, DMA1_CHANNEL6, DMA1_CHANNEL7, DMA1MUX_CHANNEL6, DMA1MUX_CHANNEL7, \
    DMAMUX_DMAREQ_ID_UART8_RX, DMAMUX_DMAREQ_ID_UART8_TX }
#define UART_S_HW { \
    USART3, CRM_USART3_PERIPH_CLOCK, GPIOC, GPIO_PINS_10, GPIO_PINS_11, \
    GPIO_PINS_SOURCE10, GPIO_PINS_SOURCE11, GPIO_MUX_7, \
    DMA2, DMA2_CHANNEL1, DMA2_CHANNEL2, DMA2MUX_CHANNEL1, DMA2MUX_CHANNEL2, \
    DMAMUX_DMAREQ_ID_USART3_RX, DMAMUX_DMAREQ_ID_USART3_TX }

#endif
