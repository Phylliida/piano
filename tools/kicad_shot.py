"""Used while teaching PCB layout: launch KiCad with GDK_BACKEND=x11, then
    python3 tools/kicad_shot.py OUT.png "PCB Editor"

Capture ONE KiCad window's own pixels (X11/XWayland), nothing else.

Privacy rules, by construction:
  * windows are filtered by WM_CLASS first; only classes containing "kicad",
    "pcbnew" or "eeschema" are ever looked at further (titles of other
    windows are never read or printed)
  * the image comes from XGetImage on that one window -- never the root
    window, never a screen region -- so an overlapping window can't leak in
    (XWayland keeps every window's contents in its own pixmap)

usage: python3 kicad_shot.py OUT.png [title-substring, default "PCB Editor"]
"""
import ctypes as C
import sys

from PIL import Image

X = C.CDLL("/run/current-system/sw/lib/libX11.so.6")
X.XOpenDisplay.restype = C.c_void_p
X.XOpenDisplay.argtypes = [C.c_char_p]
X.XDefaultRootWindow.restype = C.c_ulong
X.XDefaultRootWindow.argtypes = [C.c_void_p]
X.XInternAtom.restype = C.c_ulong
X.XInternAtom.argtypes = [C.c_void_p, C.c_char_p, C.c_int]
X.XGetWindowProperty.argtypes = [C.c_void_p, C.c_ulong, C.c_ulong, C.c_long, C.c_long, C.c_int, C.c_ulong,
                                 C.POINTER(C.c_ulong), C.POINTER(C.c_int), C.POINTER(C.c_ulong),
                                 C.POINTER(C.c_ulong), C.POINTER(C.c_void_p)]
X.XGetGeometry.argtypes = [C.c_void_p, C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_int), C.POINTER(C.c_int),
                           C.POINTER(C.c_uint), C.POINTER(C.c_uint), C.POINTER(C.c_uint), C.POINTER(C.c_uint)]


class XImage(C.Structure):
    _fields_ = [("width", C.c_int), ("height", C.c_int), ("xoffset", C.c_int), ("format", C.c_int),
                ("data", C.c_void_p), ("byte_order", C.c_int), ("bitmap_unit", C.c_int),
                ("bitmap_bit_order", C.c_int), ("bitmap_pad", C.c_int), ("depth", C.c_int),
                ("bytes_per_line", C.c_int), ("bits_per_pixel", C.c_int),
                ("red_mask", C.c_ulong), ("green_mask", C.c_ulong), ("blue_mask", C.c_ulong)]


X.XGetImage.restype = C.POINTER(XImage)
X.XGetImage.argtypes = [C.c_void_p, C.c_ulong, C.c_int, C.c_int, C.c_uint, C.c_uint, C.c_ulong, C.c_int]
ERR = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)
errors = []
handler = ERR(lambda d, e: errors.append(1) or 0)
X.XSetErrorHandler(handler)

dpy = X.XOpenDisplay(b":0")
assert dpy, "cannot open X display :0"
root = X.XDefaultRootWindow(dpy)


def prop(win, name, typ, longs=4096):
    atype, fmt, n, after, data = C.c_ulong(), C.c_int(), C.c_ulong(), C.c_ulong(), C.c_void_p()
    if X.XGetWindowProperty(dpy, win, X.XInternAtom(dpy, name, 0), 0, longs, 0, typ, C.byref(atype),
                            C.byref(fmt), C.byref(n), C.byref(after), C.byref(data)) != 0 or not data:
        return None, 0, 0
    return data, n.value, fmt.value


ANY = 0
data, n, fmt = prop(root, b"_NET_CLIENT_LIST", 33)      # XA_WINDOW
wins = [C.cast(data, C.POINTER(C.c_ulong))[i] for i in range(n)] if data else []
want = (sys.argv[2] if len(sys.argv) > 2 else "PCB Editor").lower()
found = []
for w in wins:
    d, n, _ = prop(w, b"WM_CLASS", 31)                   # XA_STRING
    cls = C.string_at(d, n).decode("latin-1", "replace").lower() if d else ""
    if not any(k in cls for k in ("kicad", "pcbnew", "eeschema")):
        continue                                         # not KiCad: never look further
    d, n, _ = prop(w, b"_NET_WM_NAME", X.XInternAtom(dpy, b"UTF8_STRING", 0))
    title = C.string_at(d, n).decode("utf-8", "replace") if d else ""
    found.append((w, title))
print("KiCad windows:", [t for _, t in found])
target = [w for w, t in found if want in t.lower()]
if not target:
    sys.exit("no KiCad window with %r in its title" % want)
w = target[0]
r, x, y, wd, ht, bw, depth = C.c_ulong(), C.c_int(), C.c_int(), C.c_uint(), C.c_uint(), C.c_uint(), C.c_uint()
X.XGetGeometry(dpy, w, C.byref(r), C.byref(x), C.byref(y), C.byref(wd), C.byref(ht), C.byref(bw), C.byref(depth))
img = X.XGetImage(dpy, w, 0, 0, wd.value, ht.value, 0xFFFFFFFF, 2)     # ZPixmap
X.XSync(dpy, 0)
if errors or not img:
    sys.exit("could not read that window (is it minimised?)")
im = img.contents
assert im.bits_per_pixel == 32, im.bits_per_pixel
buf = C.string_at(im.data, im.bytes_per_line * im.height)
pic = Image.frombuffer("RGB", (im.width, im.height), buf, "raw", "BGRX", im.bytes_per_line, 1)
pic.save(sys.argv[1])
print("saved %dx%d -> %s" % (im.width, im.height, sys.argv[1]))
