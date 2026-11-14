#!/usr/bin/env bash
# Download the two outside libraries the firmware builds against, into
# firmware/deps/ (git-ignored). Nothing of them is kept in this repository.
#
#   deps/artery/    ArteryTek AT32F402_405 firmware library (BSD-3-Clause):
#                   register headers, drivers, startup code, linker script.
#   deps/tinyusb/   TinyUSB 0.21.0 (MIT): the USB device stack for the hub.
#
# Each is one tarball of a pinned commit, checked against a sha256 recorded
# here before anything is unpacked. Only the directories the build reads are
# unpacked. Nothing in them is ever executed: our Makefile compiles their C
# sources by path, and that is all.
#
# Run it as:  bash tools/fetch_deps.sh        (from firmware/, or anywhere)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
deps="$here/../deps"

ARTERY_SHA=5dd9d55a2ce9ffa8fe0cb2652ac142920f2094a3   # v2.1.5, master on 2026-07-16
ARTERY_URL="https://codeload.github.com/ArteryTek/AT32F402_405_Firmware_Library/tar.gz/$ARTERY_SHA"
ARTERY_SUM=ff7c3187313c9d74a8269a0fea714fb4fe539cb2a5ee6ff5569cbc23f3fb4e62

TINYUSB_SHA=dae3f9a366bfcddbf9dcf1b48d7500286a849539  # tag 0.21.0
TINYUSB_URL="https://codeload.github.com/hathach/tinyusb/tar.gz/$TINYUSB_SHA"
TINYUSB_SUM=efcaf00d35fc8f0e2d1ffc3c052f4dcb3672038ee567070dc0bf1cad1b403824

# fetch NAME URL SUM TOP MEMBER...
# Download (unless a good copy is already there), verify, and unpack the
# named members of the tarball's top directory TOP into deps/NAME.
fetch() {
    local name="$1" url="$2" sum="$3" top="$4"
    shift 4
    local tarball="$deps/dl/$name.tar.gz"
    mkdir -p "$deps/dl"
    if ! echo "$sum  $tarball" | sha256sum --check --status 2>/dev/null; then
        echo "downloading $name"
        curl --fail --location --silent --show-error --output "$tarball.part" "$url"
        mv "$tarball.part" "$tarball"
    fi
    if ! echo "$sum  $tarball" | sha256sum --check --status; then
        echo "error: $tarball does not have the recorded sha256" >&2
        echo "  wanted $sum" >&2
        echo "  got    $(sha256sum "$tarball" | cut -d' ' -f1)" >&2
        exit 1
    fi
    local members=()
    local m
    for m in "$@"; do
        members+=("$top/$m")
    done
    rm -rf "${deps:?}/$name"
    mkdir -p "$deps/$name"
    tar --extract --gzip --file "$tarball" --directory "$deps/$name" \
        --strip-components=1 --no-same-owner --no-same-permissions "${members[@]}"
    echo "$name: ok ($sum)"
}

fetch artery "$ARTERY_URL" "$ARTERY_SUM" "AT32F402_405_Firmware_Library-$ARTERY_SHA" \
    LICENSE libraries/cmsis libraries/drivers

fetch tinyusb "$TINYUSB_URL" "$TINYUSB_SUM" "tinyusb-$TINYUSB_SHA" \
    LICENSE src
