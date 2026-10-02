#!/usr/bin/env bash
# The whole suite, on invented data: no network, no keyring, no Monarch account, no browser.
#
#   ./run-tests.sh [PYTHON]
#
# PYTHON must have engine/requirements.txt installed (T9 checks the read-only guard against the
# pinned graphql-core). Default: ./.venv/bin/python, else the installed engine's venv.
#   T9   the model against an independent oracle, hand-pinned cases, and its mutants
#   T10  the bar tick end to end (face states, fetch gates, files, log, config), and its mutants
#   open the two ways "Open Monarch" can go, and install-webapp.sh, with recorders on PATH
# The gesture test is separate (tools/gesture_test.py): it needs a running Omarchy shell.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${1:-}"
if [[ -z $PY ]]; then
  for candidate in "$ROOT/.venv/bin/python" "$HOME/.local/share/monarch-now/venv/bin/python"; do
    [[ -x $candidate ]] && { PY=$candidate; break; }
  done
fi
[[ -n $PY && -x $PY ]] || { echo "run-tests.sh: no python with the requirements; see README, Tests" >&2; exit 2; }
PY="$(realpath -s "$PY")"       # a venv python reached through ".." makes Python warn about its prefix

rc=0
"$PY" -B -W error::SyntaxWarning "$ROOT/engine/tests/t9_monarch_model.py" "$ROOT/engine" || rc=1
"$PY" -B -W error::SyntaxWarning "$ROOT/engine/tests/t10_monarch_face.py" "$ROOT/engine" "$ROOT/engine/monarch-now" || rc=1
"$ROOT/tools/open_test.sh" || rc=1
echo
[[ $rc -eq 0 ]] && echo "ALL SUITES PASS" || echo "SOME SUITE FAILED"
exit $rc
