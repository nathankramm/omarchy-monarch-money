#!/usr/bin/env bash
# Install (or update) Pupa's engine, monarch-now, from this checkout. Idempotent. No root.
#   venv      ~/.local/share/monarch-now/venv         built from engine/requirements.txt with
#                                                     --require-hashes (every wheel checked)
#   package   ~/.local/share/monarch-now/monarchnow   a copy of engine/monarchnow
#   launcher  ~/.local/bin/monarch-now
# It does not touch shell.json, the keyring, the cache or your config. Run it again after
# `omarchy plugin update pupa.monarch`.
set -euo pipefail
export LC_ALL=C       # one file order everywhere, so the fingerprint printed below is comparable
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/engine"
DST="$HOME/.local/share/monarch-now"
BIN="$HOME/.local/bin"
PY="$(command -v "${PYTHON:-python3}" || true)"

[ -n "$PY" ] || { echo "install.sh: python3 not found" >&2; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' ||
  { echo "install.sh: Python 3.11 or newer is needed ($("$PY" --version 2>&1) found)" >&2; exit 1; }

mkdir -p "$DST" "$BIN"
if command -v uv >/dev/null 2>&1; then
  [ -x "$DST/venv/bin/python" ] || uv venv --python "$PY" "$DST/venv" -q
  uv pip install --python "$DST/venv/bin/python" --require-hashes -r "$SRC/requirements.txt" -q
else
  [ -x "$DST/venv/bin/python" ] || "$PY" -m venv "$DST/venv"
  "$DST/venv/bin/python" -m pip install -q --disable-pip-version-check --require-hashes -r "$SRC/requirements.txt"
fi
cp "$SRC/requirements.txt" "$DST/requirements.txt"

rm -rf "$DST/monarchnow.new"
mkdir "$DST/monarchnow.new"
cp "$SRC"/monarchnow/*.py "$DST/monarchnow.new/"
rm -rf "$DST/monarchnow.old"
[ -d "$DST/monarchnow" ] && mv "$DST/monarchnow" "$DST/monarchnow.old"
mv "$DST/monarchnow.new" "$DST/monarchnow"
rm -rf "$DST/monarchnow.old"

install -m 0755 "$SRC/monarch-now" "$BIN/monarch-now"

echo "engine:   $DST/monarchnow ($(cd "$DST/monarchnow" && sha256sum ./*.py | sha256sum | cut -c1-16), a hash of the package's file hashes)"
echo "launcher: $BIN/monarch-now"
"$DST/venv/bin/python" -c 'import monarchmoney, gql, aiohttp; print("client:   monarchmoneycommunity, gql", gql.__version__, "aiohttp", aiohttp.__version__)'
command -v secret-tool >/dev/null 2>&1 || echo "note:     secret-tool (libsecret) is not installed; \`monarch-now login\` needs it"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "note:     $BIN is not on your PATH; run the engine as $BIN/monarch-now" ;; esac
echo "next:     monarch-now login"
