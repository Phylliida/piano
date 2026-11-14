/* Where the tiles are: DESIGN.md, "Tiles and where they are".
 *
 * Every tile tells the hub who is on each of its four ports. Starting from
 * the tile on the hub's own cable, the hub walks those lists: a tile on
 * another's E port is one to the right, on its N port one up. A cable that
 * does not fit (E to N, or one that would put two tiles in one place) is
 * written down as a problem and not followed. */
#include "hub.h"

#include <string.h>

static const int8_t step_u[TILE_PORTS] = { -1, 0, 1, 0 };      /* W, N, E, S */
static const int8_t step_v[TILE_PORTS] = { 0, 1, 0, -1 };

#define OPPOSITE(port)  ((uint8_t)((port) ^ 2u))               /* W <-> E, N <-> S */

/* Can this tile go on the grid: the hub knows its neighbours, and it has
 * the key layout the note arithmetic is written for. */
static bool usable(const hub_tile_t *t)
{
    return t->have_info && t->cols == KEYS_COLS && t->rows == KEYS_ROWS;
}

static void problem(hub_t *h, uint8_t what, const hub_tile_t *t, uint8_t port,
                    const hub_tile_t *n, uint8_t n_port)
{
    hub_problem_t p = { what, t->id, n->id, port, n_port };

    /* Both ends of a cable report it: keep one, the lower ID first. */
    if (p.a > p.b) {
        p.a = n->id;
        p.b = t->id;
        p.a_port = n_port;
        p.b_port = port;
    }
    for (uint8_t i = 0; i < h->nproblems; i++) {
        const hub_problem_t *q = &h->problem[i];
        if (q->a == p.a && q->b == p.b && q->a_port == p.a_port && q->b_port == p.b_port)
            return;
    }
    if (h->nproblems < HUB_PROBLEMS)
        h->problem[h->nproblems++] = p;
}

static hub_tile_t *tile_at(hub_t *h, int u, int v)
{
    for (int i = 0; i < HUB_MAX_TILES; i++) {
        hub_tile_t *t = &h->tile[i];
        if (t->used && t->placed && t->u == u && t->v == v)
            return t;
    }
    return NULL;
}

void grid_layout(hub_t *h)
{
    hub_tile_t *todo[HUB_MAX_TILES];
    int head = 0, count = 0;

    for (int i = 0; i < HUB_MAX_TILES; i++)
        h->tile[i].placed = false;
    h->nproblems = 0;
    h->width = h->height = 0;

    hub_tile_t *root = hub_find(h, hub_root(h));
    if (!root || !usable(root))
        return;
    root->placed = true;
    root->u = root->v = 0;
    todo[count++] = root;

    /* Nearest tiles first, so a tile lands where the shortest chain of
     * cables from the hub puts it. Coordinates stay within +-31. */
    while (head < count) {
        hub_tile_t *t = todo[head++];
        for (uint8_t p = 0; p < TILE_PORTS; p++) {
            hub_tile_t *n = t->nbr_id[p] ? hub_find(h, t->nbr_id[p]) : NULL;
            if (!n || n == t || !usable(n))
                continue;               /* nobody there, or not heard from yet */
            if (t->nbr_port[p] != OPPOSITE(p)) {
                problem(h, PROBLEM_PORTS, t, p, n, t->nbr_port[p]);
                continue;
            }
            int u = t->u + step_u[p], v = t->v + step_v[p];
            if (n->placed) {
                if (n->u != u || n->v != v)
                    problem(h, PROBLEM_PLACE, t, p, n, t->nbr_port[p]);
                continue;
            }
            if (tile_at(h, u, v)) {
                problem(h, PROBLEM_PLACE, t, p, n, t->nbr_port[p]);
                continue;
            }
            n->placed = true;
            n->u = (int8_t)u;
            n->v = (int8_t)v;
            todo[count++] = n;
        }
    }

    /* The lowest u and v become 0. */
    int min_u = 0, min_v = 0, max_u = 0, max_v = 0;
    for (int i = 0; i < count; i++) {
        if (todo[i]->u < min_u) min_u = todo[i]->u;
        if (todo[i]->u > max_u) max_u = todo[i]->u;
        if (todo[i]->v < min_v) min_v = todo[i]->v;
        if (todo[i]->v > max_v) max_v = todo[i]->v;
    }
    for (int i = 0; i < count; i++) {
        todo[i]->u = (int8_t)(todo[i]->u - min_u);
        todo[i]->v = (int8_t)(todo[i]->v - min_v);
    }
    h->width = (uint8_t)(max_u - min_u + 1);
    h->height = (uint8_t)(max_v - min_v + 1);
}
