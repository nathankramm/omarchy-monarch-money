#!/usr/bin/env bash
# The README's screenshots, rendered from the INVENTED household (tools/demo.py): no real
# account, merchant or amount is ever on screen. Needs the widget on a top bar of a running
# Omarchy shell, plus grim, jq and ImageMagick.
#
#   tools/screenshots.sh [OUT_DIR]        default: docs/
#
# It drives the widget through its own IPC (`fixture`, `hovercard`), captures with grim, and
# switches the fixture off again. Park the pointer away from the middle of the bar first.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/docs}"
mkdir -p "$OUT"
ipc() { omarchy-shell pupa.monarch "$@"; }
st() { ipc status | jq -r "$1"; }
trap 'ipc hovercard close || true; ipc fixture off || true' EXIT

scene() {       # scene NAME: paint it and wait for the widget to take it
  # A command set while an engine run is in flight is not picked up until the next tick, so
  # wait for the widget to be idle first, and check afterwards that a new run happened.
  local runs i
  for i in $(seq 1 60); do [[ $(st .running) == false ]] && break; sleep 0.5; done
  runs=$(st .runs)
  ipc fixture off
  sleep 0.3
  for i in $(seq 1 60); do [[ $(st .running) == false ]] && break; sleep 0.5; done
  ipc fixture "python3 '$ROOT/tools/demo.py' $1"
  for i in $(seq 1 40); do [[ $(st .running) == false && $(st .runs) -gt $runs && $(st .fixture) == true ]] && break; sleep 0.25; done
  sleep 0.5
}

card() {        # card FILE: the open card with the stretch of bar above it
  ipc hovercard open
  sleep 1.2
  local x y w h
  x=$(st .face_x); y=$(st .face_y); w=$(st .card_width); h=$(st .card_height)
  grim -g "$((x - w / 2 - 24)),0 $((w + 48))x$((2 * y + 5 + h + 24))" "$OUT/$1"
  ipc hovercard close
  sleep 0.4
}

face() {        # face FILE: the butterfly alone, as it sits on the bar
  local x y
  x=$(st .face_x); y=$(st .face_y)
  grim -g "$((x - 13)),0 26x$((2 * y))" "$OUT/$1"
}

TMP="$(mktemp -d)"
scene under;      card pupa-card.png;            OUT="$TMP" face good.png
x=$(st .face_x); y=$(st .face_y)
grim -g "$((x - 190)),0 380x$((2 * y))" "$OUT/pupa-bar.png"
scene over;       card pupa-card-over.png;       OUT="$TMP" face over.png
scene grace;      card pupa-card-grace.png;      OUT="$TMP" face plain.png
scene stale;      card pupa-card-stale.png;      OUT="$TMP" face stale.png
scene signed-out; card pupa-card-signed-out.png; OUT="$TMP" face broken.png

# the five faces side by side, each named, in the bar's own font when fontconfig can find it
FONT="$(fc-match -f '%{file}' monospace 2>/dev/null || true)"
montage -background '#1a1b26' -fill '#a9b1d6' ${FONT:+-font "$FONT"} -pointsize 20 -geometry '160x52+12+10' -tile 5x1 \
  -label 'under / on pace' "$TMP/good.png" -label 'over' "$TMP/over.png" \
  -label 'days 1-3' "$TMP/plain.png" -label 'stale' "$TMP/stale.png" \
  -label 'broken' "$TMP/broken.png" "$OUT/pupa-faces.png"
rm -rf "$TMP"
ls -1 "$OUT"/pupa-*.png
