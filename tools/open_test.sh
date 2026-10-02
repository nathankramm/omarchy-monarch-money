#!/usr/bin/env bash
# The two ways bin/monarch-open can go, and install-webapp.sh, with nothing real launched:
# the Omarchy helpers, xdg-open and the web app installer are replaced by recorders on PATH,
# HOME is a temp directory, and PATH holds nothing else of Omarchy's. No network, no browser,
# and no web app is created for real.
#
#   tools/open_test.sh        exit 0 when every check passes
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPEN="$ROOT/bin/monarch-open"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fails=0
check() { if [[ $2 == "$3" ]]; then echo "PASS  $1"; else echo "FAIL  $1"; echo "      expected: $3"; echo "      got:      $2"; fails=$((fails + 1)); fi; }

# recorders: each writes its own name and arguments to $TMP/calls
mkdir -p "$TMP/bin" "$TMP/nohelper" "$TMP/home/.local/share/applications"
for tool in omarchy-launch-or-focus-webapp xdg-open; do
  printf '#!/usr/bin/env bash\nprintf "%%s" "%s" >>"%s/calls"; printf " %%s" "$@" >>"%s/calls"; echo >>"%s/calls"\n' "$tool" "$TMP" "$TMP" "$TMP" >"$TMP/bin/$tool"
done
cp "$TMP/bin/xdg-open" "$TMP/nohelper/xdg-open"
# the installer recorder also does what the real one does that matters here: it writes the launcher
cat >"$TMP/bin/omarchy-webapp-install" <<STUB
#!/usr/bin/env bash
printf "omarchy-webapp-install" >>"$TMP/calls"; printf " %s" "\$@" >>"$TMP/calls"; echo >>"$TMP/calls"
mkdir -p "\$HOME/.local/share/applications"
printf '[Desktop Entry]\nVersion=1.0\nName=%s\nComment=%s\nExec=omarchy-launch-webapp "%s"\nTerminal=false\nType=Application\nIcon=monarch\nStartupNotify=true\n' "\$1" "\$1" "\$2" >"\$HOME/.local/share/applications/\$1.desktop"
STUB
chmod +x "$TMP"/bin/* "$TMP"/nohelper/*
# The scripts under test see ONLY the recorders and these few tools: on Omarchy the real
# helpers live in /usr/bin, and a test that reached them would launch a browser for real.
mkdir -p "$TMP/sys"
for tool in bash env sed head cat mkdir; do ln -s "$(command -v "$tool")" "$TMP/sys/$tool"; done
BASE_PATH="$TMP/sys"
run() { : >"$TMP/calls"; env -i HOME="$TMP/home" PATH="$1" "${@:2}" >"$TMP/out" 2>&1; echo $? >"$TMP/rc"; }
calls() { cat "$TMP/calls"; }

# ---- 1. no web app installed: the default browser
run "$TMP/bin:$BASE_PATH" "$OPEN" --print
check "no web app: --print says browser" "$(cat "$TMP/out")" "browser https://app.monarch.com"
check "no web app: --print opens nothing" "$(calls)" ""
run "$TMP/bin:$BASE_PATH" "$OPEN"
check "no web app: xdg-open gets the URL" "$(calls)" "xdg-open https://app.monarch.com"
check "no web app: exit 0" "$(cat "$TMP/rc")" "0"

# ---- 2. install-webapp.sh creates the web app through Omarchy's installer, icon by URL
run "$TMP/bin:$BASE_PATH" "$ROOT/install-webapp.sh"
check "install-webapp: exit 0" "$(cat "$TMP/rc")" "0"
check "install-webapp: Omarchy's installer, the icon named by URL" "$(calls)" "omarchy-webapp-install Monarch https://app.monarch.com https://static.monarch.com/logo512.png"
check "install-webapp: the launcher is the one Omarchy writes" "$(grep '^Exec=' "$TMP/home/.local/share/applications/Monarch.desktop")" 'Exec=omarchy-launch-webapp "https://app.monarch.com"'
run "$TMP/bin:$BASE_PATH" "$ROOT/install-webapp.sh"
check "install-webapp twice: nothing is reinstalled" "$(calls)" ""
run "$TMP/bin:$BASE_PATH" "$ROOT/install-webapp.sh" --force
check "install-webapp --force: reinstalled" "$(calls | cut -d' ' -f1-2)" "omarchy-webapp-install Monarch"
check "no image is bundled in the repository" "$(cd "$ROOT" && git ls-files 2>/dev/null | grep -i -E 'monarch[^/]*\.(png|svg|ico|jpg)$|logo' || true)" ""

# ---- 3. web app installed: Omarchy's launch-or-focus helper, by window class
run "$TMP/bin:$BASE_PATH" "$OPEN" --print
check "web app: --print says webapp, the class pattern and the URL" "$(cat "$TMP/out")" "webapp app.monarch.com__ https://app.monarch.com"
run "$TMP/bin:$BASE_PATH" "$OPEN"
check "web app: launch-or-focus gets the class pattern and the URL" "$(calls)" "omarchy-launch-or-focus-webapp app.monarch.com__ https://app.monarch.com"

# ---- 4. the URL is read from the launcher, so an edited launcher is followed
sed -i 's|https://app.monarch.com|https://app.monarch.com/dashboard|' "$TMP/home/.local/share/applications/Monarch.desktop"
run "$TMP/bin:$BASE_PATH" "$OPEN"
check "an edited launcher: its URL is used" "$(calls)" "omarchy-launch-or-focus-webapp app.monarch.com__ https://app.monarch.com/dashboard"

# ---- 5. a launcher but no Omarchy helper (not on Omarchy), or a launcher that is not a web app
run "$TMP/nohelper:$BASE_PATH" "$OPEN"
check "launcher without the Omarchy helper: the browser" "$(calls)" "xdg-open https://app.monarch.com"
printf '[Desktop Entry]\nName=Monarch\nExec=some-other-app\n' >"$TMP/home/.local/share/applications/Monarch.desktop"
run "$TMP/bin:$BASE_PATH" "$OPEN"
check "a Monarch launcher that is not a web app: the browser" "$(calls)" "xdg-open https://app.monarch.com"

# ---- 6. not on Omarchy at all: install-webapp.sh says so and creates nothing
rm -f "$TMP/home/.local/share/applications/Monarch.desktop"
run "$TMP/nohelper:$BASE_PATH" "$ROOT/install-webapp.sh"
check "install-webapp without Omarchy: exit 1" "$(cat "$TMP/rc")" "1"
check "install-webapp without Omarchy: nothing created" "$(ls "$TMP/home/.local/share/applications")" ""

echo
if (( fails )); then echo "open test: $fails FAILED"; exit 1; fi
echo "open test: ALL PASS"
