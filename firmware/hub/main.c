/* The hub's program on the AT32F405: the cable to the tiles, USB (a serial
 * console and a MIDI port), the two power inputs, two LEDs and a button,
 * all handed to core/hub.c.
 *
 * Nothing here has run on a board yet. */
#include <string.h>

#include "board.h"
#include "flash.h"
#include "hub.h"
#include "i2c.h"
#include "port.h"
#include "tusb.h"
#include "uart.h"
#include "usb.h"

#define CABLE_BAUD  1000000u    /* what uart_init() sets, and what the bridge gives back */

static hub_t hub;
static const uart_hw_t cable_hw = LINK_HW;
static uart_t cable;

/* ------------------------------------------------------------- measuring ---
 *
 * The ADC goes round its seven inputs for ever, 252 of its clocks (9.3 us)
 * each, and DMA writes the values into a ring that holds eight rounds:
 * the last half millisecond. Nothing interrupts; once a millisecond the
 * program reads the ring and takes each input's mean. */
#define ADC_ROUNDS  8

static uint16_t adc_ring[ADC_ROUNDS * ADC_N];
static bool ilim_low;                   /* ILIM_HI is being pulled low */

static void adc_start(void)
{
    static const adc_channel_select_type channel[ADC_N] = ADC_CHANNELS;
    dma_init_type d;
    adc_base_config_type a;
    uint32_t t0;

    port_pins(GPIOC, ADC_GPIOC_PINS, GPIO_MODE_ANALOG, GPIO_OUTPUT_PUSH_PULL, GPIO_PULL_NONE);
    port_pins(GPIOA, ADC_GPIOA_PINS, GPIO_MODE_ANALOG, GPIO_OUTPUT_PUSH_PULL, GPIO_PULL_NONE);

    dma_reset(DMA1_CHANNEL1);
    dma_default_para_init(&d);
    d.buffer_size = ADC_ROUNDS * ADC_N;
    d.direction = DMA_DIR_PERIPHERAL_TO_MEMORY;
    d.memory_base_addr = (uint32_t)adc_ring;
    d.memory_data_width = DMA_MEMORY_DATA_WIDTH_HALFWORD;
    d.memory_inc_enable = TRUE;
    d.peripheral_base_addr = (uint32_t)&ADC1->odt;
    d.peripheral_data_width = DMA_PERIPHERAL_DATA_WIDTH_HALFWORD;
    d.peripheral_inc_enable = FALSE;
    d.priority = DMA_PRIORITY_MEDIUM;
    d.loop_mode_enable = TRUE;
    dma_init(DMA1_CHANNEL1, &d);
    dma_flexible_config(DMA1, DMA1MUX_CHANNEL1, DMAMUX_DMAREQ_ID_ADC1);

    /* The ADC's clock is 216 MHz / 8 = 27 MHz. The longest sample time it
     * has, 239.5 clocks, because four of the inputs sit behind 10k. The
     * order of the calls is that of Artery's example
     * adc/repeat_conversion_loop_transfer. */
    crm_periph_clock_enable(CRM_ADC1_PERIPH_CLOCK, TRUE);
    adc_reset(ADC1);
    adc_clock_div_set(ADC_DIV_8);
    adc_base_default_para_init(&a);
    a.sequence_mode = TRUE;
    a.repeat_mode = TRUE;
    a.data_align = ADC_RIGHT_ALIGNMENT;
    a.ordinary_channel_length = ADC_N;
    adc_base_config(ADC1, &a);
    for (unsigned i = 0; i < ADC_N; i++)
        adc_ordinary_channel_set(ADC1, channel[i], (uint8_t)(i + 1), ADC_SAMPLETIME_239_5);
    adc_ordinary_conversion_trigger_set(ADC1, ADC12_ORDINARY_TRIG_SOFTWARE, TRUE);
    adc_dma_mode_enable(ADC1, TRUE);
    adc_enable(ADC1, TRUE);
    adc_calibration_init(ADC1);
    while (adc_calibration_init_status_get(ADC1)) {
    }
    adc_calibration_start(ADC1);
    while (adc_calibration_status_get(ADC1)) {
    }
    dma_channel_enable(DMA1_CHANNEL1, TRUE);
    adc_ordinary_software_trigger_enable(ADC1, TRUE);

    /* The ring is full after half a millisecond; nobody should read zeros
     * and take them for an empty socket. */
    t0 = port_ms();
    while (port_ms() - t0 < 2) {
    }
}

/* One input's pin voltage in mV (0..3300): the mean of the ring's rounds. */
static uint32_t adc_mv(unsigned input)
{
    uint32_t sum = 0;

    for (unsigned i = 0; i < ADC_ROUNDS; i++)
        sum += adc_ring[i * ADC_N + input];
    return sum * 3300u / (4095u * ADC_ROUNDS);
}

/* The voltage in front of a divider, from the voltage at its tap. */
static uint16_t undivided(uint32_t mv, uint32_t top, uint32_t bottom)
{
    return (uint16_t)(mv * (top + bottom) / bottom);
}

/* The current through an eFuse in mA, from the voltage on its ILM pin: the
 * pin puts out 182 uA for each ampere, into `ohm`. */
static uint16_t ilm_ma(uint32_t mv, uint32_t ohm)
{
    return (uint16_t)(mv * 1000000u / (IMON_GAIN_UA_PER_A * ohm));
}

static void measure(power_in_t *in)
{
    /* with ILIM_HI low the second resistor is beside the first */
    uint32_t r_host = ilim_low ? R_ILM_HOST * R_ILM_HOST_HI / (R_ILM_HOST + R_ILM_HOST_HI)
                               : R_ILM_HOST;

    in->vin_mv = undivided(adc_mv(ADC_VIN), VIN_DIVIDER_TOP, VIN_DIVIDER_BOTTOM);
    in->vpd_mv = undivided(adc_mv(ADC_VPD), VIN_DIVIDER_TOP, VIN_DIVIDER_BOTTOM);
    in->vhost_mv = undivided(adc_mv(ADC_VBUS), VBUS_DIVIDER_TOP, VBUS_DIVIDER_BOTTOM);
    in->ipd_ma = ilm_ma(adc_mv(ADC_IMON_PD), R_ILM_PD);
    in->ihost_ma = ilm_ma(adc_mv(ADC_IMON_HOST), r_host);
    in->cc1_mv = (uint16_t)adc_mv(ADC_CC1);
    in->cc2_mv = (uint16_t)adc_mv(ADC_CC2);
    in->pd_good = gpio_input_data_bit_read(PD_PG_GPIO, PD_PG_PIN) == RESET;
    in->flt_pd = gpio_input_data_bit_read(FLT_GPIO, FLT_PD_PIN) == RESET;
    in->flt_host = gpio_input_data_bit_read(FLT_GPIO, FLT_HOST_PIN) == RESET;
    in->usb_configured = tud_mounted();
    in->usb_suspended = tud_suspended();
}

/* ------------------------------------------------------------------- pins --- */

static void pins_start(void)
{
    /* Both eFuses off, as the board's pull-downs had them. */
    gpio_bits_reset(EN_GPIO, EN_PD_PIN | EN_HOST_PIN);
    port_pins(EN_GPIO, EN_PD_PIN | EN_HOST_PIN, GPIO_MODE_OUTPUT, GPIO_OUTPUT_PUSH_PULL,
              GPIO_PULL_NONE);

    /* Open drain and let go: 0.5 A from the computer. */
    gpio_bits_set(ILIM_HI_GPIO, ILIM_HI_PIN);
    port_pins(ILIM_HI_GPIO, ILIM_HI_PIN, GPIO_MODE_OUTPUT, GPIO_OUTPUT_OPEN_DRAIN,
              GPIO_PULL_NONE);

    /* The inputs have their pull-ups on the board. */
    port_pins(FLT_GPIO, FLT_PD_PIN | FLT_HOST_PIN, GPIO_MODE_INPUT, GPIO_OUTPUT_PUSH_PULL,
              GPIO_PULL_NONE);
    port_pins(PD_PG_GPIO, PD_PG_PIN, GPIO_MODE_INPUT, GPIO_OUTPUT_PUSH_PULL, GPIO_PULL_NONE);
    port_pins(BTN_USER_GPIO, BTN_USER_PIN, GPIO_MODE_INPUT, GPIO_OUTPUT_PUSH_PULL,
              GPIO_PULL_NONE);

    gpio_bits_reset(LED_GPIO, LED_STATUS_PIN | LED_POWER_PIN);
    port_pins(LED_GPIO, LED_STATUS_PIN | LED_POWER_PIN, GPIO_MODE_OUTPUT, GPIO_OUTPUT_PUSH_PULL,
              GPIO_PULL_NONE);
}

static void power_pins(const power_out_t *out)
{
    gpio_bits_write(EN_GPIO, EN_PD_PIN, out->en_pd ? TRUE : FALSE);
    gpio_bits_write(EN_GPIO, EN_HOST_PIN, out->en_host ? TRUE : FALSE);
    gpio_bits_write(ILIM_HI_GPIO, ILIM_HI_PIN, out->ilim_hi ? FALSE : TRUE);
    ilim_low = out->ilim_hi;
}

/* True once for each press of the USER button: 20 ms low in a row. */
#define BUTTON_MS  20

static bool button_pressed(void)
{
    static uint8_t low_ms;
    static bool told;

    if (gpio_input_data_bit_read(BTN_USER_GPIO, BTN_USER_PIN) != RESET) {
        low_ms = 0;
        told = false;
        return false;
    }
    if (low_ms < BUTTON_MS)
        low_ms++;
    if (low_ms < BUTTON_MS || told)
        return false;
    told = true;
    return true;
}

/* ----------------------------------------------------------------- bridge ---
 *
 * After the console's `bridge` the serial port is a plain UART on the
 * cable, for a program that talks to a tile's ROM bootloader. It goes
 *   SOON     the command's `ok` is still on its way to the computer,
 *   ON       bytes go straight through, both ways,
 *   ENDING   the last bytes are leaving the UART,
 * and back to OFF. It ends when the program on the computer closes the
 * serial port after having sent something through, or with the USER button.
 * A terminal can so type `bridge`, close, and leave the port to a flashing
 * program: closing before a byte went through ends nothing. */
enum { BRIDGE_OFF, BRIDGE_SOON, BRIDGE_ON, BRIDGE_ENDING };

#define BRIDGE_SOON_MS  100u    /* the `ok` gets this long to leave */

static uint8_t bridge;
static uint32_t bridge_asked;
static bool bridge_used;        /* a byte from the computer went through */
static bool bridge_skip_lf;     /* `bridge` ended with CR: an LF right behind it is not data */

/* Bytes from the computer that nobody has taken yet: the console's while it
 * is busy with a command, the bridge's while the UART is sending. */
static uint8_t typed[CFG_TUD_CDC_EP_BUFSIZE];
static size_t typed_len, typed_at;

static void typed_fill(void)
{
    if (typed_at < typed_len)
        return;
    typed_at = 0;
    typed_len = tud_cdc_available() ? tud_cdc_read(typed, sizeof typed) : 0;
}

static void bridge_run(uint32_t now)
{
    uint8_t buf[CFG_TUD_CDC_EP_BUFSIZE];
    size_t room, n;

    if (bridge == BRIDGE_SOON) {
        bool said = hub.out_count == 0 || !tud_mounted() || now - bridge_asked >= BRIDGE_SOON_MS;

        if (!said || !uart_tx_idle(&cable))
            return;
        uart_setup(&cable, hub.bridge.baud, hub.bridge.parity, hub.bridge.swapped);
        usb_serial_closed();            /* a close before now is not about the bridge */
        bridge_used = false;
        bridge = BRIDGE_ON;
    }

    if (bridge == BRIDGE_ENDING) {
        if (!uart_tx_idle(&cable))
            return;
        uart_setup(&cable, CABLE_BAUD, 'n', hub_swapped(&hub));
        typed_at = typed_len = 0;       /* what was on its way to the bridge is nobody's */
        tud_cdc_read_flush();
        bridge = BRIDGE_OFF;
        hub_bridge_end(&hub, now);
        return;
    }

    /* computer to cable: one UART buffer at a time */
    if (uart_tx_idle(&cable)) {
        typed_fill();
        if (bridge_skip_lf && typed_at < typed_len) {
            if (typed[typed_at] == '\n')
                typed_at++;
            bridge_skip_lf = false;
        }
        if (typed_at < typed_len) {
            uart_write(&cable, typed + typed_at, typed_len - typed_at);
            typed_at = typed_len;
            bridge_used = true;
        }
    }

    /* cable to computer: as much as USB has room for; the rest waits in
     * the UART's ring */
    room = tud_cdc_write_available();
    if (room > sizeof buf)
        room = sizeof buf;
    n = room ? uart_read(&cable, buf, room) : 0;
    if (n) {
        tud_cdc_write(buf, (uint32_t)n);
        tud_cdc_write_flush();
    }

    if (usb_serial_closed() && bridge_used)
        bridge = BRIDGE_ENDING;
}

/* ------------------------------------------------- the cable, the console --- */

static void cable_run(uint32_t now)
{
    static uint8_t buf[256];
    uint8_t wire[FRAME_MAX_WIRE];
    size_t n;

    n = uart_read(&cable, buf, sizeof buf);
    if (n)
        hub_rx(&hub, buf, n, now);

    /* A transmitter that is free first turns its pins round, if the link
     * wants them round, and then takes the next frame. */
    if (!uart_tx_idle(&cable))
        return;
    if (cable.swapped != hub_swapped(&hub))
        uart_swap(&cable, hub_swapped(&hub));
    n = hub_tx(&hub, wire);
    if (n)
        uart_write(&cable, wire, n);
}

/* What was typed goes to the console one character at a time: a command
 * that talks to tiles makes the rest wait, and what follows `bridge` is
 * the bridge's. */
static void console_in(uint32_t now)
{
    if (hub.bridging)
        return;                         /* `bridge` was typed: the rest waits for the bridge */
    typed_fill();
    while (typed_at < typed_len) {
        uint8_t c = typed[typed_at];

        if (!hub_console_rx(&hub, &c, 1, now))
            return;                     /* busy: offer it again later */
        typed_at++;
        if (hub.bridging) {
            bridge_skip_lf = c == '\r';
            return;
        }
    }
}

/* The console's text for the computer. With no computer there (USB not set
 * up) it is thrown away, so that old text does not wait for the next one. */
static void console_out(void)
{
    uint8_t buf[CFG_TUD_CDC_EP_BUFSIZE];
    size_t room = sizeof buf, n;

    if (tud_mounted() && tud_cdc_write_available() < room)
        room = tud_cdc_write_available();
    n = room ? hub_console_read(&hub, buf, room) : 0;
    if (!n)
        return;
    if (tud_mounted()) {
        tud_cdc_write(buf, (uint32_t)n);
        tud_cdc_write_flush();
    } else {
        hub.stats.out_lost += (uint32_t)n;
    }
}

/* ------------------------------------------------------------------- MIDI ---
 *
 * A message waits here while USB's buffer (64 messages) is full. If it is
 * still full after a quarter of a second, no program is taking MIDI from
 * the computer's driver: from then on messages are dropped and counted,
 * so that a program started later does not get minutes-old notes in one
 * burst. What tells that somebody listens again is an "all notes off"
 * that fits into the buffer; it also ends any note whose end was dropped. */
#define MIDI_STUCK_MS  250u

static uint8_t midi_packet[4];
static bool midi_waiting;       /* midi_packet has not gone into USB's buffer yet */
static uint32_t midi_waiting_since;
static bool midi_deaf;          /* nobody takes MIDI: it is being dropped */

static void midi_run(uint32_t now)
{
    uint8_t msg[3], in[4];

    while (tud_midi_packet_read(in)) {
        /* the hub does nothing with MIDI from the computer */
    }

    if (!tud_midi_mounted()) {
        midi_deaf = false;
        midi_waiting = false;
        while (hub_midi_get(&hub, msg))
            hub.stats.midi_lost++;
        return;
    }

    if (midi_deaf) {
        const uint8_t off[4] = { 0x0B, (uint8_t)(0xB0u | hub.set.channel), 123, 0 };

        if (tud_midi_packet_write(off)) {
            midi_deaf = false;
        } else {
            while (hub_midi_get(&hub, msg))
                hub.stats.midi_lost++;
            return;
        }
    }

    for (;;) {
        if (!midi_waiting) {
            if (!hub_midi_get(&hub, msg))
                return;
            /* On USB a message is four bytes: its kind (the status byte's
             * upper half, for the messages the hub makes), then itself. */
            midi_packet[0] = msg[0] >> 4;
            midi_packet[1] = msg[0];
            midi_packet[2] = msg[1];
            midi_packet[3] = msg[2];
            midi_waiting = true;
            midi_waiting_since = now;
        }
        if (!tud_midi_packet_write(midi_packet)) {
            if (now - midi_waiting_since >= MIDI_STUCK_MS) {
                midi_deaf = true;
                midi_waiting = false;
                hub.stats.midi_lost++;
            }
            return;
        }
        midi_waiting = false;
    }
}

/* --------------------------------------------------------------- the loop --- */

/* Everything that must not wait: USB, the cable, the console, MIDI. The
 * main loop runs it every time round, and so does the I2C driver while it
 * waits for the CH224A (a transfer takes 4 ms). So nothing in here may
 * use I2C or do the once-a-millisecond work. */
static void pump(void)
{
    uint32_t now = port_ms();

    tud_task();
    if (bridge == BRIDGE_OFF) {
        cable_run(now);
        console_in(now);
    } else {
        bridge_run(now);
    }
    if (bridge == BRIDGE_OFF || bridge == BRIDGE_SOON)
        console_out();
    midi_run(now);
}

/* Write the settings. The chip stands still for most of the 20 ms this
 * takes; USB's and the cable's bytes wait in their buffers meanwhile. */
static void save(void)
{
    uint8_t image[STORE_HUB_LEN];

    hub_save_image(&hub, image);
    hub_save_done(&hub, flash_save(image, sizeof image));
}

/* Restart, into this program or the ROM's. The computer first sees the
 * device unplugged for a moment, so that it asks the next one who it is.
 * The eFuses go off with the reset (their pull-downs): so do the tiles. */
static void leave(void (*go)(void))
{
    uint32_t t0 = port_ms();

    tud_disconnect();
    while (port_ms() - t0 < 50) {
    }
    go();
}

/* POWER: lit while the tiles are fed, blinking fast while the hub waits to
 * try again after a fault, slowly once it has given up, else dark.
 * STATUS: lit with at least one tile on the cable, blinking slowly with
 * none, flickering while the bridge is on, dark while the computer has not
 * set USB up. */
static void leds(uint32_t now)
{
    bool slow = (now >> 9) & 1, fast = (now >> 6) & 1;
    bool power, status, tiles = false;

    for (int i = 0; i < HUB_MAX_TILES; i++)
        tiles = tiles || (hub.tile[i].used && !hub.tile[i].silent);

    if (hub.power.path != PWR_NONE)
        power = true;
    else if (hub.power.hold == PWR_RETRY)
        power = fast;
    else if (hub.power.hold == PWR_LOCKED)
        power = slow;
    else
        power = false;

    if (bridge != BRIDGE_OFF)
        status = fast;
    else if (!tud_mounted())
        status = false;
    else
        status = tiles || slow;

    gpio_bits_write(LED_GPIO, LED_POWER_PIN, power ? TRUE : FALSE);
    gpio_bits_write(LED_GPIO, LED_STATUS_PIN, status ? TRUE : FALSE);
}

/* The once-a-millisecond work. */
static void tick(uint32_t now)
{
    power_in_t in;
    power_out_t out;
    unsigned act;

    measure(&in);
    power_step(&hub.power, &in, now, &out);
    power_pins(&out);

    /* The CH224A lives on the charger: no charger, no I2C. One transfer at
     * most, every 100 ms; pump() runs while it lasts. */
    ch224_poll(&hub.pd, in.vpd_mv >= PWR_PRESENT_MV, out.pd_volts, now);
    now = port_ms();

    hub_tick(&hub, now);
    act = hub_actions(&hub);
    if (act & HUB_ACT_BRIDGE) {
        bridge = BRIDGE_SOON;
        bridge_asked = now;
    }
    if (act & HUB_ACT_SAVE)
        save();
    if (act & HUB_ACT_BOOTLOADER)
        leave(port_bootloader);
    else if (act & HUB_ACT_REBOOT)
        leave(port_reboot);

    /* The button: out of the bridge, or else every note off. */
    if (button_pressed()) {
        if (bridge == BRIDGE_ON)
            bridge = BRIDGE_ENDING;
        else if (bridge == BRIDGE_OFF)
            hub_panic(&hub);
    }
    leds(now);
}

int main(void)
{
    uint8_t uid[12];
    uint32_t last_tick;

    port_boot_check();
    port_init(true);
    port_uid(uid);
    pins_start();
    uart_init(&cable, &cable_hw);
    adc_start();
    i2c1_start(pump);
    hub_init(&hub, uid, flash_record(), STORE_HUB_LEN, port_ms());
    ch224_init(&hub.pd, i2c1_xfer, NULL, port_ms());
    usb_start(uid);
    last_tick = port_ms();

    for (;;) {
        uint32_t now;

        pump();
        now = port_ms();
        if (now != last_tick) {
            last_tick = now;
            tick(now);
        }
    }
}
