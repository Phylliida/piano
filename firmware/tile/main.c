/* The tile's program on the AT32F405: scans the 40 keys, runs the four
 * ports, and hands both to core/tile.c.
 *
 * Nothing here has run on a board yet. */
#include <string.h>

#include "board.h"
#include "flash.h"
#include "port.h"
#include "tile.h"
#include "uart.h"

/* ------------------------------------------------------------------- scan ---
 *
 * One select value puts one key of each row on the five mux outputs. A
 * timer starts the ADC 16 000 times a second; each time the ADC converts
 * the five outputs one after the other (16 us in all) and DMA stores the
 * five values. The interrupt at the end of that files them under their
 * keys and sets the next select value, which then has 46 us to settle.
 * Eight steps make a scan: 2000 scans a second. */
#define SCAN_HZ     2000u
#define STEPS       8u                      /* select values */
#define STEP_HZ     (SCAN_HZ * STEPS)

static const adc_channel_select_type adc_of_row[KEYS_ROWS] = ADC_CHANNEL_OF_ROW;
static const uint8_t channel_of_col[KEYS_ROWS][KEYS_COLS] = CHANNEL_OF_COL;

static uint8_t key_at[STEPS][KEYS_ROWS];    /* the key each row's mux reads at each select value */
static uint16_t step_adc[KEYS_ROWS];        /* where DMA puts one step's five values */
static uint16_t building[KEYS_N];           /* the scan being put together */
static uint16_t finished[KEYS_N];           /* the last whole scan */
static volatile uint32_t scans_done;
static volatile bool scan_ready;
static uint8_t step;
static uint32_t scans_missed;               /* scans that went by while flash was written */

static void select_set(uint8_t value)
{
    gpio_bits_write(SEL0_GPIO, SEL0_PIN, (value & 1) ? TRUE : FALSE);
    gpio_bits_write(SEL1_GPIO, SEL1_PIN, (value & 2) ? TRUE : FALSE);
    gpio_bits_write(SEL2_GPIO, SEL2_PIN, (value & 4) ? TRUE : FALSE);
}

void DMA1_Channel1_IRQHandler(void);
void DMA1_Channel1_IRQHandler(void)
{
    if (dma_interrupt_flag_get(DMA1_FDT1_FLAG) == RESET)
        return;
    dma_flag_clear(DMA1_FDT1_FLAG);

    for (unsigned m = 0; m < KEYS_ROWS; m++)
        building[key_at[step][m]] = step_adc[m];
    step = (uint8_t)((step + 1) % STEPS);
    select_set(step);
    if (step == 0) {
        memcpy(finished, building, sizeof finished);
        scans_done++;
        scan_ready = true;
    }
}

static void scan_start(void)
{
    dma_init_type d;
    adc_base_config_type a;

    for (unsigned m = 0; m < KEYS_ROWS; m++) {
        for (unsigned c = 0; c < KEYS_COLS; c++)
            key_at[channel_of_col[m][c]][m] = (uint8_t)(KEYS_ROWS * c + m);
    }

    /* the pins: three outputs, five analog inputs (analog already) */
    port_pins(SEL0_GPIO, SEL0_PIN, GPIO_MODE_OUTPUT, GPIO_OUTPUT_PUSH_PULL, GPIO_PULL_NONE);
    port_pins(SEL1_GPIO, SEL1_PIN | SEL2_PIN, GPIO_MODE_OUTPUT, GPIO_OUTPUT_PUSH_PULL,
              GPIO_PULL_NONE);
    port_pins(GPIOA, ADC_GPIOA_PINS, GPIO_MODE_ANALOG, GPIO_OUTPUT_PUSH_PULL, GPIO_PULL_NONE);
    port_pins(GPIOB, ADC_GPIOB_PINS, GPIO_MODE_ANALOG, GPIO_OUTPUT_PUSH_PULL, GPIO_PULL_NONE);
    step = 0;
    select_set(0);

    /* DMA: five values, round and round, an interrupt after the fifth */
    dma_reset(DMA1_CHANNEL1);
    dma_default_para_init(&d);
    d.buffer_size = KEYS_ROWS;
    d.direction = DMA_DIR_PERIPHERAL_TO_MEMORY;
    d.memory_base_addr = (uint32_t)step_adc;
    d.memory_data_width = DMA_MEMORY_DATA_WIDTH_HALFWORD;
    d.memory_inc_enable = TRUE;
    d.peripheral_base_addr = (uint32_t)&ADC1->odt;
    d.peripheral_data_width = DMA_PERIPHERAL_DATA_WIDTH_HALFWORD;
    d.peripheral_inc_enable = FALSE;
    d.priority = DMA_PRIORITY_VERY_HIGH;
    d.loop_mode_enable = TRUE;
    dma_init(DMA1_CHANNEL1, &d);
    dma_flexible_config(DMA1, DMA1MUX_CHANNEL1, DMAMUX_DMAREQ_ID_ADC1);
    dma_interrupt_enable(DMA1_CHANNEL1, DMA_FDT_INT, TRUE);
    nvic_irq_enable(DMA1_Channel1_IRQn, 1, 0);

    /* The ADC: its clock is 216 MHz / 8 = 27 MHz (28 MHz is the most it
     * takes). One conversion is 71.5 + 12.5 clocks = 3.1 us, of which the
     * first 2.6 us charge the sampling capacitor through the mux. */
    crm_periph_clock_enable(CRM_ADC1_PERIPH_CLOCK, TRUE);
    adc_reset(ADC1);
    adc_clock_div_set(ADC_DIV_8);
    adc_base_default_para_init(&a);
    a.sequence_mode = TRUE;
    a.repeat_mode = FALSE;
    a.data_align = ADC_RIGHT_ALIGNMENT;
    a.ordinary_channel_length = KEYS_ROWS;
    adc_base_config(ADC1, &a);
    for (unsigned m = 0; m < KEYS_ROWS; m++)
        adc_ordinary_channel_set(ADC1, adc_of_row[m], (uint8_t)(m + 1), ADC_SAMPLETIME_71_5);
    adc_ordinary_conversion_trigger_set(ADC1, ADC12_ORDINARY_TRIG_TMR3TRGOUT, TRUE);
    adc_dma_mode_enable(ADC1, TRUE);
    adc_enable(ADC1, TRUE);
    adc_calibration_init(ADC1);
    while (adc_calibration_init_status_get(ADC1)) {
    }
    adc_calibration_start(ADC1);
    while (adc_calibration_status_get(ADC1)) {
    }
    dma_channel_enable(DMA1_CHANNEL1, TRUE);

    /* The timer: every overflow is a trigger. tmr_base_init() itself makes
     * one overflow happen, which is why the trigger output is chosen after it. */
    crm_periph_clock_enable(CRM_TMR3_PERIPH_CLOCK, TRUE);
    tmr_base_init(TMR3, PORT_TMR_HZ / STEP_HZ - 1, 0);
    tmr_cnt_dir_set(TMR3, TMR_COUNT_UP);
    tmr_primary_mode_select(TMR3, TMR_PRIMARY_SEL_OVERFLOW);
    tmr_counter_enable(TMR3, TRUE);
}

/* ------------------------------------------------------------------ ports --- */

static const uart_hw_t uart_hw[TILE_PORTS] = {
    [PORT_W] = UART_W_HW,
    [PORT_N] = UART_N_HW,
    [PORT_E] = UART_E_HW,
    [PORT_S] = UART_S_HW,
};
static uart_t uart[TILE_PORTS];

static tile_t tile;

/* Write the record. The chip stands still for most of the 20 ms this takes:
 * bytes that arrive wait in the UARTs' rings, the scans of that time are
 * lost, and the scan counter has to be told how many they were. */
static void save(void)
{
    static uint8_t image[STORE_TILE_LEN];
    uint32_t t0 = port_ms(), s0 = scans_done;

    tile_save_image(&tile, image);
    bool ok = flash_save(image, sizeof image);

    uint32_t due = (port_ms() - t0) * (SCAN_HZ / 1000u), came = scans_done - s0;
    if (due > came)
        scans_missed += due - came;
    tile_save_done(&tile, ok);
}

int main(void)
{
    static uint8_t buf[256];
    static uint16_t adc[KEYS_N];
    uint8_t uid[12], wire[FRAME_MAX_WIRE];
    uint32_t last_tick;

    port_boot_check();
    port_init(false);
    port_uid(uid);
    for (unsigned p = 0; p < TILE_PORTS; p++)
        uart_init(&uart[p], &uart_hw[p]);
    tile_init(&tile, uid, flash_record(), STORE_TILE_LEN, SCAN_HZ, port_ms());
    scan_start();
    last_tick = port_ms();

    for (;;) {
        uint32_t now = port_ms();

        for (unsigned p = 0; p < TILE_PORTS; p++) {
            size_t n = uart_read(&uart[p], buf, sizeof buf);
            if (n)
                tile_rx(&tile, (uint8_t)p, buf, n, now);
        }

        if (scan_ready) {
            uint32_t no;

            __disable_irq();
            memcpy(adc, finished, sizeof adc);
            no = scans_done + scans_missed;
            scan_ready = false;
            __enable_irq();
            tile_scan(&tile, no, adc, now);
        }

        if (now != last_tick) {
            last_tick = now;
            tile_tick(&tile, now);

            unsigned act = tile_actions(&tile);
            if (act & TILE_ACT_SAVE)
                save();
            if (act & TILE_ACT_BOOTLOADER)
                port_bootloader();
            else if (act & TILE_ACT_REBOOT)
                port_reboot();
        }

        /* A transmitter that is free first turns its pins round, if the
         * link wants them round, and then takes the next frame. */
        for (unsigned p = 0; p < TILE_PORTS; p++) {
            if (!uart_tx_idle(&uart[p]))
                continue;
            bool swapped = tile_swapped(&tile, (uint8_t)p);
            if (uart[p].swapped != swapped)
                uart_swap(&uart[p], swapped);
            size_t n = tile_tx(&tile, (uint8_t)p, wire);
            if (n)
                uart_write(&uart[p], wire, n);
        }
    }
}
