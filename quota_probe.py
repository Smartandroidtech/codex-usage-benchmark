#!/usr/bin/env python3
"""Fresh, non-generative account rate-limit reads via the Codex app-server.

The `codex exec` rollout files record rate-limit snapshots that the backend
returned *inside* a completed turn. Those bytes are immutable once the rollout
closes: waiting after the run and re-reading the file can never change them.

`codex app-server` exposes an `account/rateLimits/read` JSON-RPC method that
returns the backend's *current* account rate limits without starting a model
turn, so it consumes no model quota. This module wraps that call and normalizes
the camelCase app-server payload into the same snake_case shape used by the
in-run rollout snapshots, so the two can be compared field for field.

The pure functions (`normalize_fresh_snapshot`, `windows_match`) are import- and
test-friendly; `read_account_rate_limits` performs the subprocess I/O.
"""
import datetime as dt
import json
import subprocess

# resets_at is a unix-second timestamp that the backend re-derives on every read;
# observed jitter between two reads of the same window is <= 1 second. A window
# *change* is a jump of hours, so a few seconds of tolerance separates the two.
RESET_JITTER_TOLERANCE_SECONDS = 5


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _window(raw):
    if not isinstance(raw, dict):
        return {}
    used = raw.get("usedPercent")
    return {
        "used_percent": used,
        "resets_at": raw.get("resetsAt"),
        "window_duration_mins": raw.get("windowDurationMins"),
    }


def normalize_fresh_snapshot(response_result, limit_id="codex"):
    """Map an `account/rateLimits/read` result into the rollout snapshot shape.

    Selects the bucket named ``limit_id`` from ``rateLimitsByLimitId`` when
    present (so we never attribute quota to an alternate metered pool), falling
    back to the backward-compatible single-bucket ``rateLimits`` view. Returns
    ``None`` when no usable snapshot is present.
    """
    if not isinstance(response_result, dict):
        return None
    by_id = response_result.get("rateLimitsByLimitId")
    bucket = None
    if isinstance(by_id, dict) and limit_id in by_id:
        bucket = by_id[limit_id]
    if bucket is None:
        bucket = response_result.get("rateLimits")
    if not isinstance(bucket, dict):
        return None
    primary = _window(bucket.get("primary"))
    secondary = _window(bucket.get("secondary"))
    if primary.get("used_percent") is None and secondary.get("used_percent") is None:
        return None
    credits = response_result.get("rateLimitResetCredits") or {}
    return {
        "primary": primary,
        "secondary": secondary,
        "limit_id": bucket.get("limitId"),
        "plan_type": bucket.get("planType"),
        "rate_limit_reached_type": bucket.get("rateLimitReachedType"),
        "reset_credit_available_count": credits.get("availableCount")
        if isinstance(credits, dict) else None,
        "bucket_ids": sorted(by_id) if isinstance(by_id, dict) else [],
    }


def windows_match(reset_a, reset_b, tolerance=RESET_JITTER_TOLERANCE_SECONDS):
    """True when two reset timestamps denote the same window (within jitter)."""
    if reset_a is None or reset_b is None:
        return False
    return abs(int(reset_a) - int(reset_b)) <= tolerance


def read_account_rate_limits(settle_seconds=0, timeout=25, limit_id="codex",
                             codex_bin="codex", sleeper=None):
    """Optionally wait ``settle_seconds`` then perform one fresh, non-generative read.

    Returns a dict with ``ok`` plus either ``snapshot`` (normalized) or ``error``.
    The settle wait lets the shared backend counters settle after the launcher's
    own turn or the just-finished run before the reading is taken.
    """
    import time as _time
    sleeper = sleeper or _time.sleep
    result = {
        "ok": False, "snapshot": None, "error": None,
        "settle_seconds": settle_seconds, "limit_id": limit_id,
        "requested_at": None, "read_at": None, "raw_result": None,
    }
    if settle_seconds and settle_seconds > 0:
        sleeper(settle_seconds)
    result["requested_at"] = _now_iso()
    proc = None
    try:
        proc = subprocess.Popen(
            [codex_bin, "app-server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"clientInfo": {"name": "codex-quota-benchmark", "version": "1.0.0"}}}
        read = {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read", "params": {}}
        proc.stdin.write(json.dumps(init) + "\n")
        proc.stdin.write(json.dumps(read) + "\n")
        proc.stdin.flush()
        deadline = _time.monotonic() + timeout
        response = None
        while _time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == 2:
                response = message
                break
        result["read_at"] = _now_iso()
        if response is None:
            result["error"] = "no account/rateLimits/read response before timeout"
            return result
        if "error" in response:
            result["error"] = f"app-server error: {response['error']}"
            return result
        result["raw_result"] = response.get("result")
        snapshot = normalize_fresh_snapshot(response.get("result"), limit_id)
        if snapshot is None:
            result["error"] = "response contained no usable rate-limit snapshot"
            return result
        result["snapshot"] = snapshot
        result["ok"] = True
        return result
    except (OSError, ValueError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass
