/* notes.c: the lattice, the notes, the velocity curve. */
#include <math.h>

#include "notes.h"
#include "t.h"

static void logarithm(void)
{
    CHECK_EQ(notes_log2(1), 0);
    CHECK_EQ(notes_log2(2), 1 << 16);
    CHECK_EQ(notes_log2(1024), 10 << 16);
    CHECK_EQ(notes_log2(0x80000000u), 31u << 16);
    for (uint32_t x = 1; x < 70000; x += 7)
        CHECK_NEAR(notes_log2(x), log2((double)x) * 65536.0, 2);
    CHECK_NEAR(notes_log2(0xFFFFFFFFu), 32.0 * 65536.0, 2);
}

/* The same numbers as tilegeom.axial() gives for a 3 x 2 arrangement. */
static void lattice(void)
{
    int a, b;

    notes_lattice(0, 0, 0, &a, &b);
    CHECK_EQ(a, 0);
    CHECK_EQ(b, 0);
    notes_lattice(0, 0, 4, &a, &b);             /* column 0, row 4 */
    CHECK_EQ(a, 0);
    CHECK_EQ(b, 4);
    notes_lattice(0, 0, 5, &a, &b);             /* column 1, row 0: up and to the right of key 0 */
    CHECK_EQ(a, 1);
    CHECK_EQ(b, 0);
    notes_lattice(0, 0, 39, &a, &b);            /* column 7, row 4 */
    CHECK_EQ(a, 7);
    CHECK_EQ(b, 1);
    notes_lattice(1, 0, 0, &a, &b);             /* the next tile east */
    CHECK_EQ(a, 8);
    CHECK_EQ(b, -4);
    notes_lattice(2, 1, 13, &a, &b);            /* column 2, row 3 of tile (2, 1) */
    CHECK_EQ(a, 18);
    CHECK_EQ(b, 5 + 3 - 9);
    notes_lattice(-1, 0, 5, &a, &b);            /* floor, not truncation, west of zero */
    CHECK_EQ(a, -7);
    CHECK_EQ(b, 4);

    /* the seam: the last column of one tile and the first of the next are
     * neighbours like any two columns */
    int a0, b0, a1, b1;
    for (int r = 0; r < KEYS_ROWS; r++) {
        notes_lattice(0, 0, (uint8_t)(35 + r), &a0, &b0);
        notes_lattice(1, 0, (uint8_t)r, &a1, &b1);
        CHECK_EQ(a1 - a0, 1);
        CHECK_EQ(b1 - b0, -1);                  /* column 7 is odd: the key down and to the right */
    }
    /* and the top row of one tile with the bottom row of the tile north */
    for (int c = 0; c < KEYS_COLS; c++) {
        notes_lattice(0, 0, (uint8_t)(5 * c + 4), &a0, &b0);
        notes_lattice(0, 1, (uint8_t)(5 * c), &a1, &b1);
        CHECK_EQ(a1 - a0, 0);
        CHECK_EQ(b1 - b0, 1);
    }
}

static void notes(void)
{
    hub_settings_t s;

    settings_default(&s);
    CHECK(settings_ok(&s));
    CHECK_EQ(notes_note(&s, 0, 0, 0), 48);
    CHECK_EQ(notes_note(&s, 0, 0, 1), 48 + 7);          /* straight up: a fifth */
    CHECK_EQ(notes_note(&s, 0, 0, 5), 48 + 4);          /* up and right: a major third */
    CHECK_EQ(notes_note(&s, 1, 0, 0), 48 + 8 * 4 - 4 * 7);

    /* every key of a 4 x 3 keyboard has a note or says it has none, and
     * two tiles side by side fit in MIDI's range whole (59..87 at the east
     * end, 48..76 at the west) */
    for (int u = 0; u < 4; u++) {
        for (int v = 0; v < 3; v++) {
            for (uint8_t k = 0; k < KEYS_N; k++) {
                int n = notes_note(&s, u, v, k);
                CHECK(n >= -1 && n <= 127);
                if (u < 2 && v == 0)
                    CHECK(n >= 48 - 7 * 3 && n <= 48 + 4 * 15 + 7 * 4);
            }
        }
    }
    CHECK_EQ(notes_note(&s, 1, 0, 35), 48 + 4 * 15 - 7 * 7);
    CHECK_EQ(notes_note(&s, 1, 0, 39), 48 + 4 * 15 - 7 * 3);

    s.base = 120;
    CHECK_EQ(notes_note(&s, 0, 0, 0), 120);
    CHECK_EQ(notes_note(&s, 0, 0, 1), 127);
    CHECK_EQ(notes_note(&s, 0, 0, 2), -1);              /* 134: no such note */
    s.base = -3;
    CHECK_EQ(notes_note(&s, 0, 0, 0), -1);
    CHECK_EQ(notes_note(&s, 0, 0, 5), 1);

    /* janko: every column a semitone up, a whole tone straight up */
    s.base = 60;
    s.step_a = 1;
    s.step_b = 2;
    CHECK_EQ(notes_note(&s, 0, 0, 5), 61);
    CHECK_EQ(notes_note(&s, 0, 0, 10), 60);             /* column 2, row 0: a = 2, b = -1 */
    CHECK_EQ(notes_note(&s, 0, 0, 11), 62);
}

static void bad_settings(void)
{
    hub_settings_t s;

    settings_default(&s);
    s.vel127 = s.vel1;
    CHECK(!settings_ok(&s));
    settings_default(&s);
    s.vel1 = 0;
    CHECK(!settings_ok(&s));
    settings_default(&s);
    s.channel = 16;
    CHECK(!settings_ok(&s));
    settings_default(&s);
    s.bend = 101;
    CHECK(!settings_ok(&s));
    settings_default(&s);
    s.step_a = 25;
    CHECK(!settings_ok(&s));
    settings_default(&s);
    s.pd_volts = 20;                    /* the board cuts off above 17.8 V */
    CHECK(!settings_ok(&s));
    s.pd_volts = 9;
    CHECK(settings_ok(&s));
}

static void velocity(void)
{
    hub_settings_t s;

    settings_default(&s);
    CHECK_EQ(notes_velocity(&s, 0), 1);
    CHECK_EQ(notes_velocity(&s, 1), 1);
    CHECK_EQ(notes_velocity(&s, 100), 1);
    CHECK_EQ(notes_velocity(&s, 12000), 127);
    CHECK_EQ(notes_velocity(&s, 65535), 127);
    CHECK_EQ(notes_velocity_mid(&s), 64);

    /* against the formula, and never going down as the speed goes up */
    int last = 1;
    for (uint32_t v = 100; v <= 12000; v += 13) {
        double t = (log((double)v) - log(100.0)) / (log(12000.0) - log(100.0));
        int vel = notes_velocity(&s, (uint16_t)v);
        CHECK_NEAR(vel, 1 + 126.0 * t + 0.5, 1);
        CHECK(vel >= last);
        last = vel;
    }
    /* the middle of the curve is the geometric mean of the two speeds */
    CHECK_NEAR(notes_velocity(&s, 1095), 64, 1);

    /* a bend moves the middle and leaves the ends */
    for (int bend = -100; bend <= 100; bend += 10) {
        s.bend = (int8_t)bend;
        CHECK_EQ(notes_velocity(&s, 100), 1);
        CHECK_EQ(notes_velocity(&s, 12000), 127);
        double b = 0.5 + bend / 250.0;
        CHECK_NEAR(notes_velocity_mid(&s), 1 + 126.0 * b + 0.5, 1);
        last = 1;
        for (uint32_t v = 100; v <= 12000; v += 97) {
            int vel = notes_velocity(&s, (uint16_t)v);
            CHECK(vel >= last && vel >= 1 && vel <= 127);
            last = vel;
        }
    }
    s.bend = 50;
    CHECK(notes_velocity(&s, 1095) > 64 + 20);
    s.bend = -50;
    CHECK(notes_velocity(&s, 1095) < 64 - 20);

    /* a narrow range still works */
    s.bend = 0;
    s.vel1 = 1000;
    s.vel127 = 1001;
    CHECK_EQ(notes_velocity(&s, 999), 1);
    CHECK_EQ(notes_velocity(&s, 1001), 127);
}

int main(void)
{
    RUN(logarithm);
    RUN(lattice);
    RUN(notes);
    RUN(bad_settings);
    RUN(velocity);
    return T_DONE();
}
