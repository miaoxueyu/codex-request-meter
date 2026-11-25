#!/bin/bash
# codex-request-meter wrapper: select DeepSeek peak/off-peak pricing in Beijing time.
# Peak excludes Chinese statutory holidays; weekends always use off-peak pricing.
CFG="$HOME/.config/codex-request-meter"
VENV="${CODEX_REQUEST_METER_VENV:-$HOME/.codex/request-meter-venv}"
exec "$VENV/bin/python" -m codex_request_meter.meter --pricing-dir "$CFG" "$@"
