/* One serial port: a UART whose pins can change places, fed and drained by
 * DMA. See uart.h. */
#include "uart.h"

#include <string.h>

static void line_setup(uart_t *u, uint32_t baud, char parity, bool swapped)
{
    usart_type *usart = u->hw->usart;

    /* The pin swap (TRPSWAP) can only be written while the UART is off. */
    usart_enable(usart, FALSE);

    /* With parity the parity bit counts as a data bit: 8 data bits and
     * parity is a 9-bit word. */
    usart_init(usart, baud, parity == 'n' ? USART_DATA_8BITS : USART_DATA_9BITS, USART_STOP_1_BIT);
    usart_parity_selection_config(usart, parity == 'e'   ? USART_PARITY_EVEN
                                         : parity == 'o' ? USART_PARITY_ODD
                                                         : USART_PARITY_NONE);
    usart_transmit_receive_pin_swap(usart, swapped ? TRUE : FALSE);
    usart_transmitter_enable(usart, TRUE);
    usart_receiver_enable(usart, TRUE);
    usart_dma_transmitter_enable(usart, TRUE);
    usart_dma_receiver_enable(usart, TRUE);
    usart_enable(usart, TRUE);
    u->swapped = swapped;
}

/* Where the DMA will put the next received byte. */
static uint16_t rx_head(const uart_t *u)
{
    return (uint16_t)((UART_RX_LEN - dma_data_number_get(u->hw->rx_ch)) % UART_RX_LEN);
}

void uart_init(uart_t *u, const uart_hw_t *hw)
{
    dma_init_type d;

    u->hw = hw;
    u->rx_at = 0;
    u->tx_busy = false;
    u->swapped = false;

    crm_periph_clock_enable(hw->clock, TRUE);

    /* Both pins belong to the UART and both have a pull-up: whichever one
     * is listening reads "idle" when no cable is plugged in. */
    gpio_pin_mux_config(hw->gpio, hw->src_a, hw->mux);
    gpio_pin_mux_config(hw->gpio, hw->src_b, hw->mux);
    port_pins(hw->gpio, hw->pin_a | hw->pin_b, GPIO_MODE_MUX, GPIO_OUTPUT_PUSH_PULL, GPIO_PULL_UP);

    /* Receiving: the DMA goes round the ring for ever. */
    dma_reset(hw->rx_ch);
    dma_default_para_init(&d);
    d.buffer_size = UART_RX_LEN;
    d.direction = DMA_DIR_PERIPHERAL_TO_MEMORY;
    d.memory_base_addr = (uint32_t)u->rx;
    d.memory_data_width = DMA_MEMORY_DATA_WIDTH_BYTE;
    d.memory_inc_enable = TRUE;
    d.peripheral_base_addr = (uint32_t)&hw->usart->dt;
    d.peripheral_data_width = DMA_PERIPHERAL_DATA_WIDTH_BYTE;
    d.peripheral_inc_enable = FALSE;
    d.priority = DMA_PRIORITY_HIGH;
    d.loop_mode_enable = TRUE;
    dma_init(hw->rx_ch, &d);
    dma_flexible_config(hw->dma, hw->rx_mux, hw->rx_req);
    dma_channel_enable(hw->rx_ch, TRUE);

    /* Sending: one run of the DMA per uart_write(). */
    dma_reset(hw->tx_ch);
    d.buffer_size = 0;
    d.direction = DMA_DIR_MEMORY_TO_PERIPHERAL;
    d.memory_base_addr = (uint32_t)u->tx;
    d.priority = DMA_PRIORITY_MEDIUM;
    d.loop_mode_enable = FALSE;
    dma_init(hw->tx_ch, &d);
    dma_flexible_config(hw->dma, hw->tx_mux, hw->tx_req);

    line_setup(u, 1000000, 'n', false);
}

void uart_setup(uart_t *u, uint32_t baud, char parity, bool swapped)
{
    line_setup(u, baud, parity, swapped);
    u->rx_at = rx_head(u);
}

void uart_swap(uart_t *u, bool swapped)
{
    usart_type *usart = u->hw->usart;

    usart_enable(usart, FALSE);
    usart_transmit_receive_pin_swap(usart, swapped ? TRUE : FALSE);
    usart_enable(usart, TRUE);
    u->swapped = swapped;
}

size_t uart_read(uart_t *u, uint8_t *buf, size_t max)
{
    uint16_t head = rx_head(u);
    size_t n = 0;

    while (u->rx_at != head && n < max) {
        buf[n++] = u->rx[u->rx_at];
        u->rx_at = (uint16_t)((u->rx_at + 1) % UART_RX_LEN);
    }
    return n;
}

bool uart_tx_idle(uart_t *u)
{
    if (!u->tx_busy)
        return true;
    /* Done when the DMA has handed over the last byte and the UART says
     * the last byte's stop bit is out (TDC). */
    if (dma_data_number_get(u->hw->tx_ch) != 0 ||
        usart_flag_get(u->hw->usart, USART_TDC_FLAG) == RESET)
        return false;
    u->tx_busy = false;
    return true;
}

void uart_write(uart_t *u, const uint8_t *data, size_t len)
{
    const uart_hw_t *hw = u->hw;

    if (len == 0 || len > UART_TX_LEN)
        return;
    memcpy(u->tx, data, len);

    dma_channel_enable(hw->tx_ch, FALSE);
    /* TDC is still up from the last frame; writing 0 clears it (allowed
     * in DMA mode, reference manual 12.7.2). */
    usart_flag_clear(hw->usart, USART_TDC_FLAG);
    dma_data_number_set(hw->tx_ch, (uint16_t)len);
    dma_channel_enable(hw->tx_ch, TRUE);
    u->tx_busy = true;
}
