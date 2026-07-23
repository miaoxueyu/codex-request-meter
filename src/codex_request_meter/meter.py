#!/usr/bin/env python3
"""Measure Codex prompt usage, including thread-spawned subagents.

The script is intended to be used as a Codex command hook. Hook input is JSON
on stdin; Stop hooks return a JSON object containing ``systemMessage`` so the
summary appears in the Codex TUI transcript without becoming model context.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def zero_usage() -> dict[str, int]:
    return {field: 0 for field in USAGE_FIELDS}


def normalize_usage(value: dict[str, Any] | None) -> dict[str, int]:
    """Normalize rollout snake_case and app-server camelCase usage fields."""

    if not isinstance(value, dict):
        return zero_usage()
    result = zero_usage()
    aliases = {
        "input_tokens": ("input_tokens", "inputTokens"),
        "cached_input_tokens": ("cached_input_tokens", "cachedInputTokens"),
        "cache_write_input_tokens": (
            "cache_write_input_tokens",
            "cacheWriteInputTokens",
        ),
        "output_tokens": ("output_tokens", "outputTokens"),
        "reasoning_output_tokens": (
            "reasoning_output_tokens",
            "reasoningOutputTokens",
        ),
        "total_tokens": ("total_tokens", "totalTokens"),
    }
    for field, names in aliases.items():
        for name in names:
            raw = value.get(name)
            if isinstance(raw, (int, float)):
                result[field] = max(0, int(raw))
                break
    return result


def add_usage(*values: dict[str, int]) -> dict[str, int]:
    result = zero_usage()
    for value in values:
        for field in USAGE_FIELDS:
            result[field] += max(0, int(value.get(field, 0)))
    return result


def subtract_usage(end: dict[str, int], start: dict[str, int]) -> dict[str, int]:
    result = zero_usage()
    for field in USAGE_FIELDS:
        result[field] = max(0, int(end.get(field, 0)) - int(start.get(field, 0)))
    return result


def usage_is_zero(value: dict[str, int]) -> bool:
    return all(value.get(field, 0) == 0 for field in USAGE_FIELDS)


def _token_usage_from_event(event: dict[str, Any]) -> dict[str, int] | None:
    if event.get("type") != "event_msg":
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    total = info.get("total_token_usage") or info.get("totalTokenUsage")
    return normalize_usage(total)


def read_transcript_usage(path: str | Path | None, retries: int = 4) -> dict[str, int] | None:
    """Return the final cumulative usage in a rollout transcript."""

    if not path:
        return None
    transcript = Path(path)
    for attempt in range(max(1, retries)):
        latest: dict[str, int] | None = None
        try:
            # Token-count events are appended to the rollout. Reading from the
            # end keeps hook latency bounded for long-lived sessions.
            with transcript.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                position = handle.tell()
                buffer = b""
                while position > 0 and latest is None:
                    read_size = min(64 * 1024, position)
                    position -= read_size
                    handle.seek(position)
                    buffer = handle.read(read_size) + buffer
                    lines = buffer.split(b"\n")
                    buffer = lines[0]
                    for raw_line in reversed(lines[1:]):
                        try:
                            event = json.loads(raw_line)
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            continue
                        usage = _token_usage_from_event(event)
                        if usage is not None:
                            latest = usage
                            break
                if latest is None and buffer:
                    try:
                        usage = _token_usage_from_event(json.loads(buffer))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        usage = None
                    latest = usage
        except (FileNotFoundError, OSError):
            latest = None
        if latest is not None:
            return latest
        if attempt + 1 < retries:
            time.sleep(0.05)
    return None


def _timestamp() -> float:
    return time.time()


def _path(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


class Ledger:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "events.jsonl"
        self.lock_path = directory / ".lock"

    def _locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        lock = self.lock_path.open("a+", encoding="utf-8")
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return lock

    def append(self, record: dict[str, Any]) -> None:
        lock = self._locked()
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")
        finally:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        lock = self._locked()
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(value, dict):
                        records.append(value)
        finally:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()
        return records


@dataclass(frozen=True)
class PriceTable:
    currency: str
    models: dict[str, dict[str, float]]

    @classmethod
    def load(cls, path: Path) -> "PriceTable":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return cls("USD", {})
        currency = raw.get("currency", "USD") if isinstance(raw, dict) else "USD"
        models = raw.get("models", {}) if isinstance(raw, dict) else {}
        if not isinstance(models, dict):
            models = {}
        normalized: dict[str, dict[str, float]] = {}
        for model, rates in models.items():
            if not isinstance(model, str) or not isinstance(rates, dict):
                continue
            normalized[model] = {
                key: float(value)
                for key, value in rates.items()
                if key in {
                    "input_per_million",
                    "cached_input_per_million",
                    "cache_write_input_per_million",
                    "output_per_million",
                }
                and isinstance(value, (int, float))
            }
        return cls(str(currency), normalized)

    def rates_for(self, model: str) -> dict[str, float] | None:
        if model in self.models:
            return self.models[model]
        for pattern, rates in self.models.items():
            if pattern.endswith("*") and model.startswith(pattern[:-1]):
                return rates
        return None

    def estimate(self, model: str, usage: dict[str, int]) -> float | None:
        rates = self.rates_for(model)
        if not rates:
            return None
        required = (
            "input_per_million",
            "cached_input_per_million",
            "cache_write_input_per_million",
            "output_per_million",
        )
        if any(key not in rates for key in required):
            return None
        cached = usage.get("cached_input_tokens", 0)
        cache_write = usage.get("cache_write_input_tokens", 0)
        plain_input = max(0, usage.get("input_tokens", 0) - cached - cache_write)
        cost = plain_input * rates["input_per_million"]
        cost += cached * rates["cached_input_per_million"]
        cost += cache_write * rates["cache_write_input_per_million"]
        cost += usage.get("output_tokens", 0) * rates["output_per_million"]
        return cost / 1_000_000


def _record_kind(records: Iterable[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [record for record in records if record.get("kind") == kind]


def _find_prompt_start(records: list[dict[str, Any]], session_id: str, turn_id: str) -> dict[str, Any] | None:
    matches = [
        record
        for record in records
        if record.get("kind") == "prompt_start"
        and record.get("session_id") == session_id
        and record.get("turn_id") == turn_id
    ]
    return max(matches, key=lambda record: record.get("timestamp", 0), default=None)


def _has_prompt_complete(records: list[dict[str, Any]], session_id: str, turn_id: str) -> dict[str, Any] | None:
    matches = [
        record
        for record in records
        if record.get("kind") == "prompt_complete"
        and record.get("session_id") == session_id
        and record.get("turn_id") == turn_id
    ]
    return max(matches, key=lambda record: record.get("timestamp", 0), default=None)


def _subagent_key(record: dict[str, Any]) -> tuple[str, str]:
    return (str(record.get("agent_transcript_path") or ""), str(record.get("turn_id") or ""))


def _collect_children(
    records: list[dict[str, Any]],
    parent_path: str,
    start: float,
    end: float,
    pricing: PriceTable,
    visited: set[tuple[str, str]],
) -> tuple[dict[str, int], float | None, int, bool]:
    direct = [
        record
        for record in records
        if record.get("kind") == "subagent_complete"
        and record.get("parent_transcript_path") == parent_path
        and start <= float(record.get("timestamp", 0)) <= end
    ]
    total = zero_usage()
    total_cost = 0.0
    cost_known = True
    count = 0
    for record in direct:
        key = _subagent_key(record)
        if key in visited:
            continue
        visited.add(key)
        usage = normalize_usage(record.get("usage"))
        total = add_usage(total, usage)
        count += 1
        child_cost = record.get("cost")
        if isinstance(child_cost, (int, float)):
            total_cost += float(child_cost)
        else:
            cost_known = False
        child_path = record.get("agent_transcript_path")
        if child_path:
            nested_usage, nested_cost, nested_count, nested_known = _collect_children(
                records, child_path, start, end, pricing, visited
            )
            total = add_usage(total, nested_usage)
            count += nested_count
            if nested_cost is not None:
                total_cost += nested_cost
            else:
                cost_known = False
            cost_known = cost_known and nested_known
    return total, total_cost if cost_known else None, count, cost_known


def _format_tokens(value: int) -> str:
    return f"{max(0, int(value)):,}"


def _format_cost(value: float | None, currency: str) -> str:
    return f"{currency} {value:.4f}" if value is not None else f"{currency} n/a"


def _summary_message(
    aggregate: dict[str, int],
    cost: float | None,
    session_usage: dict[str, int],
    session_cost: float | None,
    currency: str,
    subagent_count: int,
) -> str:
    return (
        f"Prompt {_format_tokens(aggregate['total_tokens'])} tok / "
        f"{_format_cost(cost, currency)}  ["
        f"in {_format_tokens(aggregate['input_tokens'])} | "
        f"cache {_format_tokens(aggregate['cached_input_tokens'])} | "
        f"out {_format_tokens(aggregate['output_tokens'])} | "
        f"agents {subagent_count}]  "
        f"Session {_format_tokens(session_usage['total_tokens'])} tok / "
        f"{_format_cost(session_cost, currency)}"
    )


def _base_record(payload: dict[str, Any], kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "timestamp": _timestamp(),
        "session_id": str(payload.get("session_id", "")),
        "turn_id": str(payload.get("turn_id", "")),
        "model": str(payload.get("model", "")),
    }


def _handle_prompt_submit(payload: dict[str, Any], ledger: Ledger) -> None:
    record = _base_record(payload, "prompt_start")
    record["transcript_path"] = _path(payload.get("transcript_path"))
    record["baseline"] = read_transcript_usage(record["transcript_path"]) or zero_usage()
    ledger.append(record)


def _handle_subagent_start(payload: dict[str, Any], ledger: Ledger) -> None:
    record = _base_record(payload, "subagent_start")
    record["agent_id"] = str(payload.get("agent_id", ""))
    record["agent_type"] = str(payload.get("agent_type", ""))
    record["transcript_path"] = _path(payload.get("transcript_path"))
    record["baseline"] = read_transcript_usage(record["transcript_path"]) or zero_usage()
    ledger.append(record)


def _handle_subagent_stop(payload: dict[str, Any], ledger: Ledger, pricing: PriceTable) -> None:
    records = ledger.read()
    child_path = _path(payload.get("agent_transcript_path"))
    parent_path = _path(payload.get("transcript_path"))
    if not child_path or not parent_path:
        return
    if any(
        record.get("kind") == "subagent_complete"
        and record.get("agent_transcript_path") == child_path
        and record.get("turn_id") == str(payload.get("turn_id", ""))
        for record in records
    ):
        return
    starts = [
        record
        for record in records
        if record.get("kind") == "subagent_start"
        and record.get("transcript_path") == child_path
        and record.get("turn_id") == str(payload.get("turn_id", ""))
    ]
    baseline = max(starts, key=lambda record: record.get("timestamp", 0), default={}).get(
        "baseline", zero_usage()
    )
    final = read_transcript_usage(child_path)
    if final is None:
        return
    record = _base_record(payload, "subagent_complete")
    record.update(
        {
            "parent_transcript_path": parent_path,
            "agent_transcript_path": child_path,
            "agent_id": str(payload.get("agent_id", "")),
            "agent_type": str(payload.get("agent_type", "")),
            "usage": subtract_usage(final, normalize_usage(baseline)),
        }
    )
    record["cost"] = pricing.estimate(record["model"], record["usage"])
    ledger.append(record)


def _handle_stop(payload: dict[str, Any], ledger: Ledger, pricing: PriceTable) -> str:
    session_id = str(payload.get("session_id", ""))
    turn_id = str(payload.get("turn_id", ""))
    existing = _has_prompt_complete(ledger.read(), session_id, turn_id)
    if existing:
        return str(existing.get("message", "Prompt meter already recorded this turn."))

    records = ledger.read()
    start_record = _find_prompt_start(records, session_id, turn_id)
    transcript_path = _path(payload.get("transcript_path"))
    final = read_transcript_usage(transcript_path)
    if start_record is None or final is None:
        return "Prompt meter: usage unavailable for this turn (Codex continued normally)."

    end = _timestamp()
    start = float(start_record.get("timestamp", end))
    root_usage = subtract_usage(final, normalize_usage(start_record.get("baseline")))
    root_cost = pricing.estimate(str(payload.get("model", "")), root_usage)
    child_usage, child_cost, child_count, child_known = _collect_children(
        records, transcript_path or "", start, end, pricing, set()
    )
    aggregate = add_usage(root_usage, child_usage)
    cost = None if root_cost is None or not child_known else root_cost + (child_cost or 0.0)

    completed = {
        "kind": "prompt_complete",
        "timestamp": end,
        "session_id": session_id,
        "turn_id": turn_id,
        "transcript_path": transcript_path,
        "model": str(payload.get("model", "")),
        "usage": aggregate,
        "cost": cost,
        "subagent_count": child_count,
    }
    prior = [
        record
        for record in records
        if record.get("kind") == "prompt_complete" and record.get("session_id") == session_id
    ]
    session_usage = zero_usage()
    session_cost = 0.0
    session_known = True
    for record in prior:
        session_usage = add_usage(session_usage, normalize_usage(record.get("usage")))
        if isinstance(record.get("cost"), (int, float)):
            session_cost += float(record["cost"])
        else:
            session_known = False
    session_usage = add_usage(session_usage, aggregate)
    if cost is None:
        session_known = False
    else:
        session_cost += cost
    completed["message"] = _summary_message(
        aggregate,
        cost,
        session_usage,
        session_cost if session_known else None,
        pricing.currency,
        child_count,
    )
    ledger.append(completed)
    return completed["message"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pricing", type=Path, required=True)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("CODEX_METER_DATA_DIR", Path.home() / ".codex/request-meter")),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        event = payload.get("hook_event_name")
        ledger = Ledger(args.data_dir)
        pricing = PriceTable.load(args.pricing)
        if event == "UserPromptSubmit":
            _handle_prompt_submit(payload, ledger)
        elif event == "SubagentStart":
            _handle_subagent_start(payload, ledger)
        elif event == "SubagentStop":
            _handle_subagent_stop(payload, ledger, pricing)
        elif event == "Stop":
            message = _handle_stop(payload, ledger, pricing)
            print(json.dumps({"continue": True, "systemMessage": message}))
        return 0
    except Exception as error:  # Hooks must never stop the agent because metering failed.
        if payload.get("hook_event_name") == "Stop" if "payload" in locals() else False:
            message = f"Prompt meter unavailable ({type(error).__name__}); Codex continued normally."
            print(json.dumps({"continue": True, "systemMessage": message}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
