/* The whole test framework: count checks, print the ones that fail. */
#ifndef PIANO_T_H
#define PIANO_T_H

#include <stdio.h>
#include <string.h>

static int t_tests, t_checks, t_fails;
static const char *t_name = "";

#define CHECK(cond) do { \
        t_checks++; \
        if (!(cond)) { \
            t_fails++; \
            printf("  FAIL %s:%d (%s): %s\n", __FILE__, __LINE__, t_name, #cond); \
        } \
    } while (0)

#define CHECK_EQ(a, b) do { \
        long long t_a = (long long)(a), t_b = (long long)(b); \
        t_checks++; \
        if (t_a != t_b) { \
            t_fails++; \
            printf("  FAIL %s:%d (%s): %s is %lld, expected %lld (%s)\n", \
                   __FILE__, __LINE__, t_name, #a, t_a, t_b, #b); \
        } \
    } while (0)

/* a within `tol` of b */
#define CHECK_NEAR(a, b, tol) do { \
        long long t_a = (long long)(a), t_b = (long long)(b), t_t = (long long)(tol); \
        t_checks++; \
        if (t_a < t_b - t_t || t_a > t_b + t_t) { \
            t_fails++; \
            printf("  FAIL %s:%d (%s): %s is %lld, expected %lld +- %lld\n", \
                   __FILE__, __LINE__, t_name, #a, t_a, t_b, t_t); \
        } \
    } while (0)

#define RUN(fn) do { t_name = #fn; t_tests++; fn(); } while (0)

/* The last line of main(): print the totals; the exit status is 1 if
 * anything failed. */
static inline int t_done(const char *file)
{
    const char *base = strrchr(file, '/');
    printf("%-22s %3d tests %8d checks %4d failed\n", base ? base + 1 : file,
           t_tests, t_checks, t_fails);
    return t_fails != 0;
}
#define T_DONE() t_done(__FILE__)

#endif
