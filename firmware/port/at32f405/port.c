/* The AT32F405RCT7 itself: clock, time, the chip's ID, restarting. */
#include "port.h"

/* ---- going to the ROM bootloader ----
 *
 * The chip has no register that maps the boot memory in place of flash
 * (SCFG's MEM_MAP_SEL is read-only), so the way in is "method 2" of
 * Artery's note AN0008, "Jump to Boot Memory": leave a mark that survives a
 * reset, reset, and before anything is switched on (clocks at their reset
 * state, PLL off, no interrupt enabled) load the stack pointer and the start
 * address from the first two words of the boot memory.
 *
 * Three differences from the note: the mark is a word in RAM that the
 * start-up code does not clear, where the note uses a battery-domain
 * register; the jump is at the top of main(), where the note's is at the
 * top of SystemInit() (Artery's SystemInit only puts the clock back on the
 * internal oscillator); and the vector table register is pointed at the boot
 * memory, which the note leaves alone.
 *
 * Not verified: the note was written for and tried on another chip of the
 * family and names no address for this one. 0x1FFFA400 is the boot memory's
 * start in the AT32F405 datasheet (table 5, 20 KB). It has not been tried.
 * If it does not work, the BOOT0 pad still does. */
#define BOOT_ROM     0x1FFFA400u
#define BOOT_MAGIC   0xB007AB1Eu

static uint32_t boot_word __attribute__((section(".noinit")));

void port_boot_check(void)
{
    if (boot_word != BOOT_MAGIC)
        return;
    boot_word = 0;

    const uint32_t *rom = (const uint32_t *)BOOT_ROM;
    uint32_t sp = rom[0], pc = rom[1];
    SCB->VTOR = BOOT_ROM;
    __asm volatile("msr msp, %0\n"
                   "bx %1\n" : : "r"(sp), "r"(pc));
    for (;;) {
    }
}

void port_bootloader(void)
{
    boot_word = BOOT_MAGIC;
    nvic_system_reset();
}

void port_reboot(void)
{
    nvic_system_reset();
}

/* ---- clock ----
 *
 * The sequence of Artery's project template for 216 MHz from a 12 MHz
 * crystal (the same one TinyUSB's board support uses):
 * 12 MHz * 72 / 1 = 864 MHz in the PLL, / 4 = 216 MHz for the system,
 * / 18 = 48 MHz for USB. AHB and APB2 216 MHz, APB1 108 MHz. */
static void clock_start(void)
{
    crm_reset();
    flash_psr_set(FLASH_WAIT_CYCLE_6);
    crm_periph_clock_enable(CRM_PWC_PERIPH_CLOCK, TRUE);
    pwc_ldo_output_voltage_set(PWC_LDO_OUTPUT_1V3);

    crm_clock_source_enable(CRM_CLOCK_SOURCE_HEXT, TRUE);
    while (crm_hext_stable_wait() == ERROR) {
        /* no crystal, no keyboard */
    }
    crm_pll_config(CRM_PLL_SOURCE_HEXT, 72, 1, CRM_PLL_FP_4);
    crm_pllu_div_set(CRM_PLL_FU_18);
    crm_clock_source_enable(CRM_CLOCK_SOURCE_PLL, TRUE);
    while (crm_flag_get(CRM_PLL_STABLE_FLAG) != SET) {
    }

    crm_ahb_div_set(CRM_AHB_DIV_1);
    crm_apb2_div_set(CRM_APB2_DIV_1);
    crm_apb1_div_set(CRM_APB1_DIV_2);

    crm_auto_step_mode_enable(TRUE);
    crm_sysclk_switch(CRM_SCLK_PLL);
    while (crm_sysclk_switch_status_get() != CRM_SCLK_PLL) {
    }
    crm_auto_step_mode_enable(FALSE);
    system_core_clock_update();
}

static void usb_clock_start(void)
{
    crm_pllu_output_set(TRUE);
    while (crm_flag_get(CRM_PLLU_STABLE_FLAG) != SET) {
    }
    crm_usb_clock_source_select(CRM_USB_CLOCK_SOURCE_PLLU);
    crm_periph_clock_enable(CRM_OTGFS1_PERIPH_CLOCK, TRUE);
}

/* ---- time ----
 *
 * SysTick interrupts once a millisecond. While flash is erased the chip
 * stands still for up to 16 ms and those interrupts are lost, so the
 * handler does not just count: TMR4 runs free at 4 kHz, needs no
 * interrupt, and tells the handler how many milliseconds really passed. */
#define QUARTERS_HZ  4000u

static volatile uint32_t ms_now;
static uint16_t quarters_seen;      /* TMR4's count at the last millisecond counted */

void SysTick_Handler(void);
void SysTick_Handler(void)
{
    uint16_t behind = (uint16_t)((uint16_t)tmr_counter_value_get(TMR4) - quarters_seen);
    uint16_t n = behind >= 8 ? behind / 4 : 1;

    ms_now += n;
    quarters_seen = (uint16_t)(quarters_seen + 4 * n);
}

uint32_t port_ms(void)
{
    return ms_now;
}

static void time_start(void)
{
    crm_periph_clock_enable(CRM_TMR4_PERIPH_CLOCK, TRUE);
    tmr_base_init(TMR4, 0xFFFF, PORT_TMR_HZ / QUARTERS_HZ - 1);
    tmr_cnt_dir_set(TMR4, TMR_COUNT_UP);
    tmr_counter_enable(TMR4, TRUE);
    quarters_seen = (uint16_t)tmr_counter_value_get(TMR4);

    SysTick_Config(system_core_clock / 1000);
}

/* ---- pins ---- */

void port_pins(gpio_type *gpio, uint16_t pins, gpio_mode_type mode, gpio_output_type type,
               gpio_pull_type pull)
{
    gpio_init_type init;

    gpio_default_para_init(&init);
    init.gpio_pins = pins;
    init.gpio_mode = mode;
    init.gpio_out_type = type;
    init.gpio_pull = pull;
    init.gpio_drive_strength = GPIO_DRIVE_STRENGTH_MODERATE;
    gpio_init(gpio, &init);
}

/* Every pin analog (its input buffer off), so that a pin nothing drives
 * cannot float around the input threshold and draw current. Left alone:
 * PA13 and PA14 (the debugger), port F (the crystal, and BOOT0), and with
 * USB in use PA11 and PA12, which the USB controller takes by itself once
 * its clock runs (Artery's and TinyUSB's examples leave them as they are
 * after reset). The drivers then take the pins they use. */
static void pins_start(bool usb)
{
    uint16_t keep = GPIO_PINS_13 | GPIO_PINS_14;

    if (usb)
        keep |= GPIO_PINS_11 | GPIO_PINS_12;

    crm_periph_clock_enable(CRM_GPIOA_PERIPH_CLOCK, TRUE);
    crm_periph_clock_enable(CRM_GPIOB_PERIPH_CLOCK, TRUE);
    crm_periph_clock_enable(CRM_GPIOC_PERIPH_CLOCK, TRUE);
    crm_periph_clock_enable(CRM_GPIOD_PERIPH_CLOCK, TRUE);

    port_pins(GPIOA, (uint16_t)(GPIO_PINS_ALL & ~keep), GPIO_MODE_ANALOG,
              GPIO_OUTPUT_PUSH_PULL, GPIO_PULL_NONE);
    port_pins(GPIOB, GPIO_PINS_ALL, GPIO_MODE_ANALOG, GPIO_OUTPUT_PUSH_PULL, GPIO_PULL_NONE);
    port_pins(GPIOC, GPIO_PINS_ALL, GPIO_MODE_ANALOG, GPIO_OUTPUT_PUSH_PULL, GPIO_PULL_NONE);
    port_pins(GPIOD, GPIO_PINS_ALL, GPIO_MODE_ANALOG, GPIO_OUTPUT_PUSH_PULL, GPIO_PULL_NONE);
}

void port_init(bool usb)
{
    nvic_priority_group_config(NVIC_PRIORITY_GROUP_4);
    clock_start();
    if (usb)
        usb_clock_start();

    /* Neither board uses the high-speed USB controller, and its PHY draws
     * 3 mA until it is told not to. Needs the crystal running. */
    reduce_power_consumption();

    pins_start(usb);
    crm_periph_clock_enable(CRM_DMA1_PERIPH_CLOCK, TRUE);
    crm_periph_clock_enable(CRM_DMA2_PERIPH_CLOCK, TRUE);
    dmamux_enable(DMA1, TRUE);
    dmamux_enable(DMA2, TRUE);
    time_start();
}

/* ---- the chip's ID: 96 bits at 0x1FFFF7E8 ---- */

void port_uid(uint8_t uid[12])
{
    const uint8_t *p = (const uint8_t *)0x1FFFF7E8u;

    for (int i = 0; i < 12; i++)
        uid[i] = p[i];
}

/* The C library's start-up calls this before main(); the file that
 * usually holds it (crti.o) is left out of the link. */
void _init(void);
void _init(void)
{
}
