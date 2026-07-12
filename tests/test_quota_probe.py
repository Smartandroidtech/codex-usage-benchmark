#!/usr/bin/env python3
"""Offline, deterministic tests for fresh-read parsing/selection and in-run metrics.

No network, no subprocess, no model quota. Run with: python3 tests/test_quota_probe.py
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import quota_probe  # noqa: E402
import run_benchmark  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def load_result():
    payload = json.loads((FIXTURES / "account_rate_limits.response.json").read_text())
    return payload["result"]


class NormalizeFreshSnapshot(unittest.TestCase):
    def test_selects_named_limit_bucket_not_alternate_pool(self):
        snap = quota_probe.normalize_fresh_snapshot(load_result(), limit_id="codex")
        self.assertEqual(snap["limit_id"], "codex")
        self.assertEqual(snap["primary"]["used_percent"], 80)
        self.assertEqual(snap["primary"]["resets_at"], 1783825346)
        self.assertEqual(snap["secondary"]["used_percent"], 30)
        # The decoy alternate pool (used_percent 5, far-future reset) must be ignored.
        self.assertNotEqual(snap["primary"]["resets_at"], 1999999999)
        self.assertEqual(snap["bucket_ids"], ["alternate_pool", "codex"])
        self.assertEqual(snap["reset_credit_available_count"], 3)

    def test_falls_back_to_single_bucket_view(self):
        result = {"rateLimits": {"limitId": "codex",
                                 "primary": {"usedPercent": 42, "resetsAt": 111},
                                 "secondary": {"usedPercent": 7, "resetsAt": 222}}}
        snap = quota_probe.normalize_fresh_snapshot(result, limit_id="codex")
        self.assertEqual(snap["primary"]["used_percent"], 42)

    def test_missing_requested_bucket_falls_back_to_default(self):
        # Requested limit_id absent from rateLimitsByLimitId -> use single-bucket rateLimits.
        snap = quota_probe.normalize_fresh_snapshot(load_result(), limit_id="does-not-exist")
        self.assertEqual(snap["primary"]["used_percent"], 80)

    def test_returns_none_without_usable_snapshot(self):
        self.assertIsNone(quota_probe.normalize_fresh_snapshot({}, "codex"))
        self.assertIsNone(quota_probe.normalize_fresh_snapshot(None, "codex"))
        empty = {"rateLimits": {"primary": {"resets_at": 1}, "secondary": {}}}
        self.assertIsNone(quota_probe.normalize_fresh_snapshot(empty, "codex"))


class WindowsMatch(unittest.TestCase):
    def test_one_second_jitter_is_same_window(self):
        self.assertTrue(quota_probe.windows_match(1784393379, 1784393380))

    def test_hours_apart_is_a_different_window(self):
        self.assertFalse(quota_probe.windows_match(1783825346, 1783825346 + 3600))

    def test_none_never_matches(self):
        self.assertFalse(quota_probe.windows_match(None, 100))
        self.assertFalse(quota_probe.windows_match(100, None))


class ReadWithInjectedSleeper(unittest.TestCase):
    def test_settle_delay_is_requested_before_read(self):
        # Force a fast failure path (nonexistent binary) but confirm the settle
        # delay is honored via the injected sleeper without real time passing.
        calls = []
        out = quota_probe.read_account_rate_limits(
            settle_seconds=7, timeout=1, codex_bin="codex-does-not-exist-xyz",
            sleeper=lambda s: calls.append(s))
        self.assertEqual(calls, [7])
        self.assertFalse(out["ok"])
        self.assertIsNotNone(out["error"])
        self.assertEqual(out["settle_seconds"], 7)


class InRunMetricsParsing(unittest.TestCase):
    def test_metrics_extracts_rate_limits_from_event(self):
        events = [json.loads(line) for line in
                  (pathlib.Path(__file__).resolve().parent / "rollout_two_snapshots.jsonl").read_text().splitlines()]
        # Last rate-bearing event should surface primary/secondary used_percent.
        _, rate = run_benchmark.metrics(events)
        self.assertEqual(rate["primary"]["used_percent"], 78)
        self.assertEqual(rate["secondary"]["used_percent"], 30)

    def test_coherence_filter_rejects_foreign_reset_window(self):
        # Snapshots from a different reset window must not be treated as coherent.
        snapshots = [
            {"rate_limits": {"primary": {"used_percent": 78, "resets_at": 1783825346},
                             "secondary": {"used_percent": 30, "resets_at": 1784393379}}},
            {"rate_limits": {"primary": {"used_percent": 2, "resets_at": 1783825346 + 18000},
                             "secondary": {"used_percent": 1, "resets_at": 1784393379 + 604800}}},
        ]
        coherent = [s for s in snapshots
                    if s["rate_limits"]["primary"]["resets_at"] == 1783825346
                    and s["rate_limits"]["secondary"]["resets_at"] == 1784393379]
        self.assertEqual(len(coherent), 1)
        self.assertEqual(coherent[-1]["rate_limits"]["primary"]["used_percent"], 78)


if __name__ == "__main__":
    unittest.main(verbosity=2)
