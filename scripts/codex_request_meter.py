#!/usr/bin/env python3
"""Compatibility entry point for the installed package."""

from pathlib import Path
import sys

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from codex_request_meter.meter import *  # noqa: F401,F403,E402
from codex_request_meter.meter import (  # noqa: E402
    _collect_children,
    _handle_prompt_submit,
    _handle_stop,
    _handle_subagent_start,
    _handle_subagent_stop,
    read_transcript_usage,
)


if __name__ == "__main__":
    raise SystemExit(main())
