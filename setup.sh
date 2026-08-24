#!/bin/bash
# One-shot installer for a fresh machine: venv + editable install + configs + hooks.
# Uses the customized local source in this repo.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="${CODEX_REQUEST_METER_VENV:-$HOME/.codex/request-meter-venv}"
CFG="$HOME/.config/codex-request-meter"
MTR="$HOME/.codex/request-meter"

pick_python() {
  for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)' 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  echo "ERROR: need Python >= 3.10" >&2; return 1
}
PY="$(pick_python)"
echo ">> using $PY ($($PY --version 2>&1))"
echo ">> venv: $VENV"

[ -d "$VENV" ] || "$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$REPO_DIR"

mkdir -p "$CFG" "$MTR"
for f in pricing.json pricing.peak.json pricing.offpeak.json; do
  [ -f "$CFG/$f" ] || cp "$REPO_DIR/config/$f" "$CFG/$f"   # don't clobber user edits
done
cp "$REPO_DIR/config/run-meter.sh" "$MTR/run-meter.sh"
chmod +x "$MTR/run-meter.sh"

# generate hooks (merges with existing), then point all commands at the wrapper
"$VENV/bin/python" -m codex_request_meter.install >/dev/null
"$VENV/bin/python" - "$HOME/.codex/hooks.json" "$MTR/run-meter.sh" <<'PY'
import json,sys
hp, wrapper = sys.argv[1], sys.argv[2]
d = json.loads(open(hp, encoding='utf-8').read())
for event, groups in d.get('hooks', {}).items():
    for g in groups:
        for h in g.get('hooks', []):
            if h.get('type') == 'command':
                h['command'] = wrapper
open(hp, 'w', encoding='utf-8').write(json.dumps(d, ensure_ascii=False, indent=2) + '\n')
PY

    echo
    echo "Done. Next:"
    echo "  1) Start a NEW Codex session and approve the hook trust prompt if shown."
    echo "  2) Each turn will show two aligned lines:"
    echo "       Prompt:  <n> tok  CNY <c>  cache <pct>%"
    echo "       Session: <n> tok  CNY <c>  cache <pct>%"
    echo "  Prices are in $CFG/pricing.{peak,offpeak}.json (CNY per 1M tokens)."
