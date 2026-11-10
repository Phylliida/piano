#!/usr/bin/env bash
# Run kicad-cli at low priority on one core:   ./kcli.sh sch erc board.kicad_sch
# The path is this machine's NixOS store path for KiCad 9.0.6; change it to
# your own `kicad-cli` elsewhere.
set -euo pipefail
KICAD_CLI=/nix/store/2n8mb63qkhnfzb1g5cxwclgp10q5lwn2-kicad-9.0.6/bin/kicad-cli
export HOME="${KICAD_HOME:-$(dirname "$(readlink -f "$0")")/.nixhome}"
exec taskset -c 0 nice -n 19 "$KICAD_CLI" "$@"
