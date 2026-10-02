#!/usr/bin/env bash
# Optional: create the Monarch web app that a click on the butterfly and the card's
# "Open Monarch" button open (or focus). Without it they open Monarch in your default browser.
#
# It is an ordinary Omarchy web app, made by Omarchy's own installer:
#     ~/.local/share/applications/Monarch.desktop      Exec=omarchy-launch-webapp "https://app.monarch.com"
#     ~/.local/share/icons/hicolor/256x256/apps/monarch.png
# The icon is Monarch Money's own, downloaded from Monarch at install time. It is not part of
# this repository. Remove the web app again with `omarchy-webapp-remove Monarch`.
#
#   install-webapp.sh           create it (does nothing when it already exists)
#   install-webapp.sh --force   recreate it
set -euo pipefail
NAME="Monarch"
URL="https://app.monarch.com"
ICON_URL="https://static.monarch.com/logo512.png"
DESKTOP="$HOME/.local/share/applications/$NAME.desktop"

command -v omarchy-webapp-install >/dev/null 2>&1 ||
  { echo "install-webapp.sh: omarchy-webapp-install not found (this step needs Omarchy)" >&2; exit 1; }

if [[ -e $DESKTOP && ${1:-} != "--force" ]]; then
  echo "The $NAME web app is already installed: $DESKTOP"
  echo "(run with --force to recreate it)"
  exit 0
fi

# The named icon first; if Monarch has moved it, let Omarchy find the site's icon by itself.
omarchy-webapp-install "$NAME" "$URL" "$ICON_URL" || omarchy-webapp-install "$NAME" "$URL" ""

[[ -f $DESKTOP ]] || { echo "install-webapp.sh: $DESKTOP was not created" >&2; exit 1; }
echo "Created the $NAME web app: $DESKTOP"
echo "The butterfly and \"Open Monarch\" now open or focus it."
