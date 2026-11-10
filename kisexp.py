"""Read and write KiCad's s-expression files as trees, never as text.

gen_tile.py edited board files with regexes and shipped broken boards
silently, more than once: a pattern that matched nothing, a paren dropped
from a replacement.  Here a file is parsed into nested lists, edited as data,
and written back with every paren balanced by construction.

    tree = parse(text)          # (kicad_sch (version 2025...) ...) -> list
    find(tree, "lib_symbols")   # first child list whose head is that atom
    dump(tree)                  # back to text

Atoms are Sym (bare words such as `yes`, `input`) or str (quoted strings) or
int/float.  Keeping Sym and str apart is what lets dump() re-quote exactly the
atoms that were quoted.
"""

import re


class Sym(str):
    """A bare word, written without quotes."""


_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)


def parse(text):
    stack, cur = [], []
    pos, n = 0, len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip():
                raise ValueError("unparseable at %d: %r" % (pos, text[pos:pos + 40]))
            break
        pos = m.end()
        opn, cls, quoted, bare = m.groups()
        if opn:
            stack.append(cur)
            cur = []
        elif cls:
            if not stack:
                raise ValueError("unbalanced ')' at %d" % pos)
            done, cur = cur, stack.pop()
            cur.append(done)
        elif quoted is not None:
            cur.append(re.sub(r"\\(.)", r"\1", quoted))
        else:
            cur.append(_number(bare))
    if stack:
        raise ValueError("unbalanced '(': %d still open" % len(stack))
    if len(cur) != 1:
        raise ValueError("expected one top-level expression, got %d" % len(cur))
    return cur[0]


def _number(word):
    try:
        return int(word)
    except ValueError:
        pass
    try:
        return float(word)
    except ValueError:
        return Sym(word)


def _atom(a):
    if isinstance(a, Sym):
        return str(a)
    if isinstance(a, str):
        return '"%s"' % a.replace("\\", "\\\\").replace('"', '\\"')
    if isinstance(a, bool):
        return "yes" if a else "no"
    if isinstance(a, float):
        s = ("%.6f" % a).rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    return str(a)


def dump(node, indent=0):
    """Text for a tree.  Short lists of atoms stay on one line, as KiCad does."""
    if not isinstance(node, list):
        return _atom(node)
    pad = "\t" * indent
    if all(not isinstance(c, list) for c in node):
        return pad + "(" + " ".join(_atom(c) for c in node) + ")"
    head = [_atom(c) for c in node if not isinstance(c, list)]
    lines = [pad + "(" + " ".join(head)]
    for c in node:
        if isinstance(c, list):
            lines.append(dump(c, indent + 1))
    lines.append(pad + ")")
    return "\n".join(lines)


# ------------------------------------------------------------------ queries ---
def head(node):
    return node[0] if isinstance(node, list) and node else None


def children(node, name):
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


def find(node, name):
    for c in node:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return None


def walk(node):
    """Every list in the tree, depth first."""
    if isinstance(node, list):
        yield node
        for c in node:
            yield from walk(c)


def S(*items):
    """Build a node: S("at", 1.27, 2.54, 0) -> [Sym("at"), 1.27, 2.54, 0].
    The head is always a Sym; strings after it stay quoted strings."""
    return [Sym(items[0])] + list(items[1:])
