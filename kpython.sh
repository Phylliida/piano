#!/usr/bin/env bash
# Run Python with KiCad 9's scripting API (pcbnew) importable, at low priority
# on one core:   ./kpython.sh gen_pcb.py
#
# The paths are this machine's NixOS store paths, copied from the environment
# the `pcbnew` wrapper sets up.  On another machine, point them at your KiCad 9
# install: KICAD_SITE holds pcbnew.py, and PY must be the Python version KiCad
# was built against (3.13 here).
set -euo pipefail

KICAD_SITE=/nix/store/q0iynkjkg0n0r0r9hi1wpvpkhlnc0dmx-kicad-base-9.0.6/lib/python3.13/site-packages
PY=/nix/store/s4h72l9ac7jdlvzckd0bbbpnmg3xhl4p-python3-3.13.12-env/bin/python3.13
export KICAD9_FOOTPRINT_DIR=/nix/store/i0v8h57wj8pnw48865bbjdzw4i1sa61i-kicad-footprints-0836ca140b/share/kicad/footprints
export KICAD9_SYMBOL_DIR=/nix/store/6ayphbhvpaql7s42qnwxpzl731scj0b1-kicad-symbols-e92aabf81d/share/kicad/symbols
export PYTHONPATH="$KICAD_SITE${PYTHONPATH:+:$PYTHONPATH}"
# KiCad wants a writable config dir; keep it inside the project.
export HOME="${KICAD_HOME:-$(dirname "$(readlink -f "$0")")/.nixhome}"

exec taskset -c 0 nice -n 19 "$PY" "$@"
