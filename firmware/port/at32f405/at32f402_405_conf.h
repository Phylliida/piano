/* What Artery's library wants from a project: the crystal, and which of
 * its drivers to pull in. at32f402_405.h includes this file by name.
 *
 * Follows the layout of Artery's at32f402_405_conf_template.h. */
#ifndef __AT32F402_405_CONF_H
#define __AT32F402_405_CONF_H

#ifdef __cplusplus
extern "C" {
#endif

/* Both boards carry a 12 MHz crystal. */
#if !defined HEXT_VALUE
#define HEXT_VALUE               ((uint32_t)12000000)
#endif
#define HEXT_STARTUP_TIMEOUT     ((uint16_t)0x3000)
#define HICK_VALUE               ((uint32_t)8000000)
#define LEXT_VALUE               ((uint32_t)32768)

/* The drivers the port uses. The USB driver is TinyUSB's, not Artery's. */
#define ADC_MODULE_ENABLED
#define CRM_MODULE_ENABLED
#define DMA_MODULE_ENABLED
#define FLASH_MODULE_ENABLED
#define GPIO_MODULE_ENABLED
#define I2C_MODULE_ENABLED
#define MISC_MODULE_ENABLED
#define PWC_MODULE_ENABLED
#define TMR_MODULE_ENABLED
#define USART_MODULE_ENABLED

#ifdef ADC_MODULE_ENABLED
#include "at32f402_405_adc.h"
#endif
#ifdef CRM_MODULE_ENABLED
#include "at32f402_405_crm.h"
#endif
#ifdef DMA_MODULE_ENABLED
#include "at32f402_405_dma.h"
#endif
#ifdef FLASH_MODULE_ENABLED
#include "at32f402_405_flash.h"
#endif
#ifdef GPIO_MODULE_ENABLED
#include "at32f402_405_gpio.h"
#endif
#ifdef I2C_MODULE_ENABLED
#include "at32f402_405_i2c.h"
#endif
#ifdef MISC_MODULE_ENABLED
#include "at32f402_405_misc.h"
#endif
#ifdef PWC_MODULE_ENABLED
#include "at32f402_405_pwc.h"
#endif
#ifdef TMR_MODULE_ENABLED
#include "at32f402_405_tmr.h"
#endif
#ifdef USART_MODULE_ENABLED
#include "at32f402_405_usart.h"
#endif

#ifdef __cplusplus
}
#endif

#endif
