#!/usr/bin/env python3
"""Compatibility entry point for the installed package."""

from pathlib import Path
import shlex
import sys

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from codex_request_meter.install import *  # noqa: F401,F403,E402
from codex_request_meter.install import (  # noqa: E402
    EVENTS,
    merge_hooks,
    write_atomic,
)


def hook_command(pricing: Path | None = None) -> str:
    """Keep the legacy source-tree installer self-contained."""

    root = Path(__file__).resolve().parents[1]
    meter = root / "scripts" / "codex_request_meter.py"
    pricing_path = pricing or root / "meter" / "pricing.json"
    return shlex.join([sys.executable, str(meter), "--pricing", str(pricing_path)])


if __name__ == "__main__":
    raise SystemExit(main())
