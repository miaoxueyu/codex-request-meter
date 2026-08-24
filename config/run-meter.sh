#!/bin/bash
# codex-request-meter wrapper: auto-select DeepSeek peak/off-peak pricing by Beijing time.
# Peak = Mon-Fri 09:00-12:00 & 14:00-18:00 (Asia/Shanghai); otherwise off-peak.
CFG="$HOME/.config/codex-request-meter"
VENV="${CODEX_REQUEST_METER_VENV:-$HOME/.codex/request-meter-venv}"
HOUR=$(TZ=Asia/Shanghai date +%H)
HOUR=$((10#$HOUR))
DOW=$(TZ=Asia/Shanghai date +%u)   # 1=Mon .. 7=Sun
PEAK=0
if [ "$DOW" -le 5 ]; then
  if { [ "$HOUR" -ge 9 ] && [ "$HOUR" -lt 12 ]; } || { [ "$HOUR" -ge 14 ] && [ "$HOUR" -lt 18 ]; }; then
    PEAK=1
  fi
fi
if [ "$PEAK" = 1 ]; then PRICING="$CFG/pricing.peak.json"; else PRICING="$CFG/pricing.offpeak.json"; fi
exec "$VENV/bin/python" -m codex_request_meter.meter --pricing "$PRICING" "$@"
