#include "util.h"

uint32_t id_from_uid(const uint8_t uid[12])
{
    uint32_t h = 2166136261u;
    for (int i = 0; i < 12; i++) {
        h ^= uid[i];
        h *= 16777619u;
    }
    if (h == 0u)
        h = 1u;
    if (h == 0xFFFFFFFFu)
        h = 0xFFFFFFFEu;
    return h;
}

void rng_seed(rng_t *r, uint32_t seed)
{
    /* xorshift stays at 0 forever, so never start there */
    r->s = seed ? seed : 0x9E3779B9u;
    for (int i = 0; i < 4; i++)
        rng_next(r);
}

uint32_t rng_next(rng_t *r)
{
    uint32_t x = r->s;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    r->s = x;
    return x;
}

uint32_t rng_range(rng_t *r, uint32_t lo, uint32_t hi)
{
    return lo + rng_next(r) % (hi - lo + 1u);
}

size_t vfmt(char *out, size_t max, const char *f, va_list ap)
{
    size_t n = 0;

    if (max == 0)
        return 0;
    for (; *f; f++) {
        char digits[12];
        const char *s = digits;
        size_t len = 0, width = 0;
        bool zero = false;

        if (*f != '%') {
            if (n + 1 < max)
                out[n++] = *f;
            continue;
        }
        f++;
        if (*f == '0') {
            zero = true;
            f++;
        }
        while (*f >= '0' && *f <= '9')
            width = width * 10 + (size_t)(*f++ - '0');

        switch (*f) {
        case 'd':
        case 'u':
        case 'x': {
            uint32_t v;
            bool neg = false;
            unsigned base = *f == 'x' ? 16 : 10;
            if (*f == 'd') {
                int d = va_arg(ap, int);
                neg = d < 0;
                v = neg ? 0u - (uint32_t)d : (uint32_t)d;
            } else {
                v = va_arg(ap, unsigned);
            }
            /* digits, last one first, from the end of the buffer */
            char *p = digits + sizeof digits;
            do {
                *--p = "0123456789abcdef"[v % base];
                v /= base;
            } while (v);
            if (neg && !zero)
                *--p = '-';
            len = (size_t)(digits + sizeof digits - p);
            s = p;
            if (neg && zero) {
                if (n + 1 < max)
                    out[n++] = '-';
                if (width)
                    width--;
            }
            break;
        }
        case 's':
            s = va_arg(ap, const char *);
            while (s[len])
                len++;
            zero = false;
            break;
        case 'c':
            digits[0] = (char)va_arg(ap, int);
            len = 1;
            zero = false;
            break;
        case '%':
            digits[0] = '%';
            len = 1;
            zero = false;
            break;
        default:
            /* not a conversion this knows: stop rather than guess */
            out[n] = 0;
            return n;
        }
        for (; width > len; width--) {
            if (n + 1 < max)
                out[n++] = zero ? '0' : ' ';
        }
        for (size_t i = 0; i < len; i++) {
            if (n + 1 < max)
                out[n++] = s[i];
        }
    }
    out[n] = 0;
    return n;
}

size_t fmt(char *out, size_t max, const char *f, ...)
{
    va_list ap;

    va_start(ap, f);
    size_t n = vfmt(out, max, f, ap);
    va_end(ap);
    return n;
}

uint16_t crc16_ccitt(const uint8_t *data, size_t n)
{
    uint16_t crc = 0xFFFFu;
    for (size_t i = 0; i < n; i++) {
        crc ^= (uint16_t)((uint16_t)data[i] << 8);
        for (int b = 0; b < 8; b++)
            crc = (crc & 0x8000u) ? (uint16_t)((crc << 1) ^ 0x1021u) : (uint16_t)(crc << 1);
    }
    return crc;
}

uint32_t crc32_ieee(const uint8_t *data, size_t n)
{
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < n; i++) {
        crc ^= data[i];
        for (int b = 0; b < 8; b++)
            crc = (crc & 1u) ? (crc >> 1) ^ 0xEDB88320u : crc >> 1;
    }
    return ~crc;
}
