import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from codex_request_meter.install import (  # noqa: E402
    bundled_pricing_path,
    ensure_pricing,
    hook_command as package_hook_command,
)
from scripts.codex_request_meter import (
    Ledger,
    PriceTable,
    _collect_children,
    _handle_prompt_submit,
    _handle_stop,
    _handle_subagent_start,
    _handle_subagent_stop,
    read_transcript_usage,
)
from scripts.install_codex_meter import EVENTS, merge_hooks


def usage(input_tokens, cached, output, total=None):
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output,
        "reasoning_output_tokens": 0,
        "total_tokens": total if total is not None else input_tokens + output,
    }


def write_transcript(path: Path, values):
    with path.open("w", encoding="utf-8") as handle:
        for value in values:
            handle.write(
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {"total_token_usage": value},
                        },
                    }
                )
                + "\n"
            )


class MeterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.pricing_path = self.root / "pricing.json"
        self.pricing_path.write_text(
            json.dumps(
                {
                    "currency": "USD",
                    "models": {
                        "test-model": {
                            "input_per_million": 1,
                            "cached_input_per_million": 0.5,
                            "cache_write_input_per_million": 0,
                            "output_per_million": 2,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        self.pricing = PriceTable.load(self.pricing_path)
        self.transcript = self.root / "root.jsonl"
        write_transcript(self.transcript, [usage(100, 20, 10)])

    def tearDown(self):
        self.temp.cleanup()

    def test_reads_latest_cumulative_usage(self):
        write_transcript(self.transcript, [usage(100, 20, 10), usage(180, 60, 30)])
        expected = usage(180, 60, 30)
        expected["cache_write_input_tokens"] = 0
        self.assertEqual(read_transcript_usage(self.transcript), expected)

    def test_gpt56_prices_match_supplied_rates(self):
        pricing = PriceTable.load(Path(__file__).parents[1] / "meter" / "pricing.json")
        usage_value = {
            "input_tokens": 1_000_000,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "output_tokens": 1_000_000,
        }
        self.assertEqual(pricing.estimate("gpt-5.6-sol", usage_value), 35)
        self.assertEqual(pricing.estimate("gpt-5.6-terra", usage_value), 17.5)
        self.assertEqual(pricing.estimate("gpt-5.6-luna", usage_value), 7)

    def test_package_bundles_pricing_and_generates_portable_hook(self):
        target = self.root / "config" / "pricing.json"
        self.assertFalse(target.exists())
        ensure_pricing(target)
        self.assertEqual(target.read_text(encoding="utf-8"), bundled_pricing_path().read_text(encoding="utf-8"))
        command = package_hook_command(target)
        self.assertIn("-m codex_request_meter.meter", command)
        self.assertIn(str(target), command)

    def test_prompt_delta_and_session_summary(self):
        ledger = Ledger(self.data)
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session",
            "turn_id": "turn-1",
            "model": "test-model",
            "transcript_path": str(self.transcript),
        }
        _handle_prompt_submit(payload, ledger)
        write_transcript(self.transcript, [usage(100, 20, 10), usage(180, 60, 30)])
        message = _handle_stop(
            {
                "hook_event_name": "Stop",
                "session_id": "session",
                "turn_id": "turn-1",
                "model": "test-model",
                "transcript_path": str(self.transcript),
            },
            ledger,
            self.pricing,
        )
        self.assertIn("Prompt 100 tok", message)
        self.assertIn("Session 100 tok", message)
        self.assertIn("USD", message)

    def test_subagent_is_included_once(self):
        ledger = Ledger(self.data)
        child = self.root / "child.jsonl"
        write_transcript(child, [usage(0, 0, 0), usage(50, 10, 5)])
        _handle_subagent_start(
            {
                "hook_event_name": "SubagentStart",
                "session_id": "child-session",
                "turn_id": "child-turn",
                "model": "test-model",
                "agent_id": "a1",
                "agent_type": "worker",
                "transcript_path": str(child),
            },
            ledger,
        )
        write_transcript(child, [usage(0, 0, 0), usage(50, 10, 5), usage(75, 20, 9)])
        _handle_subagent_stop(
            {
                "hook_event_name": "SubagentStop",
                "session_id": "child-session",
                "turn_id": "child-turn",
                "model": "test-model",
                "agent_id": "a1",
                "agent_type": "worker",
                "transcript_path": str(self.transcript),
                "agent_transcript_path": str(child),
            },
            ledger,
            self.pricing,
        )
        records = ledger.read()
        children, cost, count, known = _collect_children(
            records, str(self.transcript), 0, 9999999999, self.pricing, set()
        )
        self.assertEqual(children["total_tokens"], 29)
        self.assertEqual(count, 1)
        self.assertTrue(known)
        self.assertIsNotNone(cost)

    def test_unknown_price_keeps_token_summary(self):
        ledger = Ledger(self.data)
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session",
            "turn_id": "turn-unknown",
            "model": "unknown-model",
            "transcript_path": str(self.transcript),
        }
        _handle_prompt_submit(payload, ledger)
        write_transcript(self.transcript, [usage(100, 20, 10), usage(180, 60, 30)])
        message = _handle_stop(
            {
                "hook_event_name": "Stop",
                "session_id": "session",
                "turn_id": "turn-unknown",
                "model": "unknown-model",
                "transcript_path": str(self.transcript),
            },
            ledger,
            self.pricing,
        )
        self.assertIn("Prompt 100 tok", message)
        self.assertIn("USD n/a", message)

    def test_installer_preserves_existing_hooks_and_is_idempotent(self):
        hooks_path = self.root / "hooks.json"
        hooks_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {
                                "hooks": [
                                    {"type": "command", "command": "existing-hook"}
                                ]
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        command = "meter-command"
        merged = merge_hooks(hooks_path, command)
        hooks_path.write_text(json.dumps(merged), encoding="utf-8")
        merged_again = merge_hooks(hooks_path, command)
        self.assertEqual(
            sum(
                handler.get("command") == "existing-hook"
                for group in merged_again["hooks"]["Stop"]
                for handler in group["hooks"]
            ),
            1,
        )
        for event in EVENTS:
            self.assertTrue(
                any(
                    handler.get("command") == command
                    for group in merged_again["hooks"][event]
                    for handler in group["hooks"]
                )
            )

    def test_installer_replaces_legacy_absolute_meter_hooks(self):
        hooks_path = self.root / "hooks.json"
        hooks_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        event: [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "/old/python scripts/codex_request_meter.py --pricing /old/meter/pricing.json",
                                    }
                                ]
                            }
                        ]
                        for event in EVENTS
                    }
                }
            ),
            encoding="utf-8",
        )
        merged = merge_hooks(hooks_path, "python -m codex_request_meter.meter")
        for event in EVENTS:
            commands = [
                handler.get("command")
                for group in merged["hooks"][event]
                for handler in group.get("hooks", [])
            ]
            self.assertNotIn("/old/python scripts/codex_request_meter.py --pricing /old/meter/pricing.json", commands)


if __name__ == "__main__":
    unittest.main()
