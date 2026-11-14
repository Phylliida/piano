/* The hub as a USB device. See usb.h.
 *
 * The IDs are 0x1209:0x0001, pid.codes' test ID: fine on a bench, not for a
 * keyboard that leaves it (README.md, "USB ID"). */
#include "usb.h"

#include <string.h>

#include "port.h"
#include "proto.h"
#include "tusb.h"

#define USB_VID         0x1209
#define USB_PID         0x0001

/* The computer sees four interfaces: a serial port is two (control and
 * data), and so is a MIDI port (an empty audio control one and the stream). */
enum { ITF_CDC, ITF_CDC_DATA, ITF_MIDI, ITF_MIDI_STREAM, ITF_COUNT };
enum { STR_LANG, STR_MAKER, STR_PRODUCT, STR_SERIAL, STR_CDC, STR_MIDI, STR_COUNT };

#define EP_CDC_NOTIFY   0x81
#define EP_CDC_OUT      0x02
#define EP_CDC_IN       0x82
#define EP_MIDI_OUT     0x03
#define EP_MIDI_IN      0x83

/* Class 0xEF/2/1 says "look at the interfaces": what a device with a serial
 * port and something else beside it has to say. */
static const tusb_desc_device_t device = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = USB_VID,
    .idProduct = USB_PID,
    .bcdDevice = FW_VERSION,
    .iManufacturer = STR_MAKER,
    .iProduct = STR_PRODUCT,
    .iSerialNumber = STR_SERIAL,
    .bNumConfigurations = 1,
};

/* One configuration. It asks for 500 mA: the tiles are fed from this
 * socket until a charger is plugged in. */
#define CONFIG_LEN  (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN + TUD_MIDI_DESC_LEN)

static const uint8_t config[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_COUNT, 0, CONFIG_LEN, 0, 500),
    TUD_CDC_DESCRIPTOR(ITF_CDC, STR_CDC, EP_CDC_NOTIFY, 8, EP_CDC_OUT, EP_CDC_IN, 64),
    TUD_MIDI_DESCRIPTOR(ITF_MIDI, STR_MIDI, EP_MIDI_OUT, EP_MIDI_IN, 64),
};

static const char *const strings[STR_COUNT] = {
    [STR_MAKER] = "Hex keyboard project",
    [STR_PRODUCT] = "Hex keyboard",
    [STR_CDC] = "Hex keyboard console",
    [STR_MIDI] = "Hex keyboard",
};
static char serial[2 * 12 + 1];

const uint8_t *tud_descriptor_device_cb(void)
{
    return (const uint8_t *)&device;
}

const uint8_t *tud_descriptor_configuration_cb(uint8_t index)
{
    (void)index;
    return config;
}

/* Strings go out as 16-bit characters behind a two-byte head. */
const uint16_t *tud_descriptor_string_cb(uint8_t index, uint16_t langid)
{
    static uint16_t out[1 + 32];
    size_t n;

    (void)langid;
    if (index == STR_LANG) {
        out[1] = 0x0409;                /* English, the only language on offer */
        n = 1;
    } else {
        if (index >= STR_COUNT)
            return NULL;
        const char *text = index == STR_SERIAL ? serial : strings[index];

        n = strlen(text);
        if (n > 32)
            n = 32;
        for (size_t i = 0; i < n; i++)
            out[1 + i] = (uint8_t)text[i];
    }
    out[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2 * n + 2));
    return out;
}

/* ---- the serial port's DTR line ----
 *
 * A program that opens the serial port raises DTR and one that closes it
 * lets it go; TinyUSB calls this from tud_task() each time. */
static bool dtr_was, closed;

void tud_cdc_line_state_cb(uint8_t itf, bool dtr, bool rts)
{
    (void)itf;
    (void)rts;
    if (dtr_was && !dtr)
        closed = true;
    dtr_was = dtr;
}

bool usb_serial_closed(void)
{
    bool was = closed;

    closed = false;
    return was;
}

/* ---- what TinyUSB needs from the chip ---- */

uint32_t tusb_time_millis_api(void)
{
    return port_ms();
}

void OTGFS1_IRQHandler(void);
void OTGFS1_IRQHandler(void)
{
    tusb_int_handler(0, true);
}

/* The controller's "general core configuration" register, and its bit that
 * says "do not wait for a voltage on the VBUS pin": at32f402_405_usb.h,
 * gccfg at offset 0x38, vbusig = bit 21. The board has no wire to that pin
 * (it measures VBUS with the ADC instead), and the hub cannot be running
 * without the computer's 5 V anyway. TinyUSB's own board file for this chip
 * sets the same bit at the same moment. */
#define OTGFS1_GCCFG    (*(volatile uint32_t *)(OTGFS1_BASE + 0x38u))
#define GCCFG_VBUSIG    (1u << 21)

void usb_start(const uint8_t uid[12])
{
    static const char hex[] = "0123456789ABCDEF";
    static const tusb_rhport_init_t rh = { .role = TUSB_ROLE_DEVICE, .speed = TUSB_SPEED_AUTO };

    for (int i = 0; i < 12; i++) {
        serial[2 * i] = hex[uid[i] >> 4];
        serial[2 * i + 1] = hex[uid[i] & 15];
    }
    serial[24] = 0;

    OTGFS1_GCCFG |= GCCFG_VBUSIG;
    tusb_init(0, &rh);
}
