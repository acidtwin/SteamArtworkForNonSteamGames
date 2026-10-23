#!/usr/bin/env bash
# Capture every README picture from your own library (see harness.py). Needs the app's venv (PySide6).
#   docs/render/real_all.sh /path/to/app/.venv/bin/python [GAME]
#   LIBRARY='{"drop": ["Maelstrom"], "add": ["Alan Wake 2"]}' docs/render/real_all.sh ...  # tidier game list
# Pictures land in docs/render/out; nothing outside docs/render and $WORK is written.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${1:?the app venv python}"
export GAME="${2:-Cyberpunk}" REAL=1
export WORK="${WORK:-$(mktemp -d)}" OUT="${OUT:-$HERE/out}"
mkdir -p "$OUT"
"$PY" -c "import PIL" 2>/dev/null || { "$PY" -m pip install -q --target "$WORK/deps" pillow; export PYTHONPATH="$WORK/deps"; }
cd "$HERE"
if [ -n "${LIBRARY:-}" ]; then
  # A changed game list (see harness.py): the added games have no artwork yet, so Get All Artwork
  # runs first and the other scenes start from the library it filled, not from your Steam folder.
  echo "== real_get (animated)"; CONF='{"get_all_animated": true}' "$PY" harness.py real_get
  export REAL_HOME="$WORK/fakehome-real_get"
fi
for scene in real_stills real_page real_pick real_tray; do
  echo "== $scene"; "$PY" harness.py "$scene"
done
[ -n "${LIBRARY:-}" ] || { echo "== real_get (animated)"; CONF='{"get_all_animated": true}' "$PY" harness.py real_get; }
rm -rf "$WORK"/fakehome-*  # the copy of your library
echo "pictures in $OUT"
