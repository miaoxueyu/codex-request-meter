#!/usr/bin/env python3
"""Install the request meter into the user's global Codex hooks."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sys
import tempfile
from pathlib import Path


EVENTS = ("UserPromptSubmit", "SubagentStart", "SubagentStop", "Stop")
APP_NAME = "codex-request-meter"


def bundled_pricing_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "pricing.json"


def config_home() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    return Path(configured) if configured else Path.home() / ".config"


def default_pricing_path() -> Path:
    return config_home() / APP_NAME / "pricing.json"


def ensure_pricing(path: Path) -> Path:
    if path.exists():
        return path
    source = bundled_pricing_path()
    if not source.exists():
        raise SystemExit(f"Bundled pricing file is missing: {source}")
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, path)
    return path


def hook_command(pricing: Path | None = None) -> str:
    return shlex.join(
        [
            sys.executable,
            "-m",
            "codex_request_meter.meter",
            "--pricing",
            str(pricing or default_pricing_path()),
        ]
    )


def is_legacy_meter_command(command: object) -> bool:
    return isinstance(command, str) and "scripts/codex_request_meter.py" in command


def merge_hooks(path: Path, command: str) -> dict:
    if path.exists():
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise SystemExit(f"Cannot parse existing hooks file {path}: {error}")
    else:
        config = {}
    if not isinstance(config, dict):
        raise SystemExit(f"Existing hooks file {path} must contain a JSON object")
    hooks = config.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SystemExit(f"Existing hooks file {path} has a non-object 'hooks' field")
    for event in EVENTS:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise SystemExit(f"Existing hooks.{event} must be an array")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            group["hooks"] = [
                handler
                for handler in group["hooks"]
                if not (
                    isinstance(handler, dict)
                    and handler.get("type") == "command"
                    and is_legacy_meter_command(handler.get("command"))
                )
            ]
        groups[:] = [
            group
            for group in groups
            if not isinstance(group, dict) or group.get("hooks")
        ]
        already_present = any(
            isinstance(group, dict)
            and any(
                isinstance(handler, dict)
                and handler.get("type") == "command"
                and handler.get("command") == command
                for handler in group.get("hooks", [])
            )
            for group in groups
        )
        if not already_present:
            groups.append({"hooks": [{"type": "command", "command": command, "timeout": 15}]})
    return config


def write_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pricing", type=Path)
    parser.add_argument("--codex-home", type=Path)
    args = parser.parse_args()
    pricing = args.pricing or default_pricing_path()
    if not args.dry_run:
        ensure_pricing(pricing)
    codex_home = args.codex_home or Path(
        os.environ.get("CODEX_HOME", Path.home() / ".codex")
    )
    path = codex_home / "hooks.json"
    command = hook_command(pricing)
    merged = merge_hooks(path, command)
    if args.dry_run:
        print(json.dumps(merged, ensure_ascii=False, indent=2))
    else:
        write_atomic(path, merged)
        print(f"Installed Codex request meter hooks in {path}")
        print(f"Using pricing table {pricing}")
        print("Start a new Codex session and approve the hook trust prompt if shown.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
