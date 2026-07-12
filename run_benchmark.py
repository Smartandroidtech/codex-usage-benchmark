#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

import quota_probe

ROOT = pathlib.Path(__file__).resolve().parent
TASKS = ("aggregate", "dependencies", "schedule")


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def walk(obj):
    yield obj
    if isinstance(obj, dict):
        for value in obj.values():
            yield from walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk(value)


def metrics(events):
    token = None
    rate = None
    for event in events:
        for item in walk(event):
            if not isinstance(item, dict):
                continue
            if "last_token_usage" in item:
                token = item.get("last_token_usage") or token
            if all(key in item for key in ("input_tokens", "cached_input_tokens", "output_tokens")):
                token = item
            candidate = item.get("rate_limits")
            if isinstance(candidate, dict) and candidate.get("primary"):
                rate = candidate
    return token or {}, rate or {}


def last_known_rate_limit(expected_primary_reset=None, expected_secondary_reset=None):
    sessions = pathlib.Path.home() / ".codex" / "sessions"
    files = sorted(sessions.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:12]
    newest = None
    for path in files:
        try:
            for line in path.read_text(errors="ignore").splitlines()[-250:]:
                event = json.loads(line)
                _, rate = metrics([event])
                if rate:
                    if expected_primary_reset is not None and rate.get("primary", {}).get("resets_at") != expected_primary_reset:
                        continue
                    if expected_secondary_reset is not None and rate.get("secondary", {}).get("resets_at") != expected_secondary_reset:
                        continue
                    stamp = event.get("timestamp", "")
                    if newest is None or stamp > newest["timestamp"]:
                        newest = {"timestamp": stamp, "rate_limits": rate, "source": str(path)}
        except (OSError, json.JSONDecodeError):
            pass
    return newest or {}


def empty_rate_meta(reason):
    return {"snapshot_count": 0, "coherent_count": 0, "reset_pairs": [], "ambiguous": False,
            "snapshots": [], "selected_snapshot": None, "selection_reason": reason,
            "session_file": None, "session_match_count": 0, "resolved": {}}


def rate_limit_for_events(events, expected_primary_reset=None, expected_secondary_reset=None):
    thread_id = next((e.get("thread_id") for e in events if e.get("type") == "thread.started"), None)
    if not thread_id:
        return {}, empty_rate_meta("thread.started missing")
    sessions = pathlib.Path.home() / ".codex" / "sessions"
    matches = sorted(sessions.rglob(f"*{thread_id}*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not matches:
        return {}, empty_rate_meta("no rollout matched thread_id")
    snapshots = []
    resolved = {}
    try:
        for line in matches[0].read_text(errors="ignore").splitlines():
            event = json.loads(line)
            _, rate = metrics([event])
            if rate:
                snapshots.append({"timestamp": event.get("timestamp"), "rate_limits": rate})
            if event.get("type") == "session_meta":
                payload = event.get("payload", {})
                resolved.update({"session_id": payload.get("session_id"), "cli_version": payload.get("cli_version"),
                                 "model_provider": payload.get("model_provider")})
            if event.get("type") == "turn_context":
                payload = event.get("payload", {})
                resolved.update({"model": payload.get("model"), "effort": payload.get("effort"),
                                 "context_window_id": payload.get("context_window", {}).get("window_id")})
            if event.get("type") == "event_msg" and event.get("payload", {}).get("type") == "token_count":
                resolved["model_context_window"] = event.get("payload", {}).get("info", {}).get("model_context_window")
    except (OSError, json.JSONDecodeError):
        return {}, empty_rate_meta("rollout read or JSON parse failure")
    coherent = [s for s in snapshots
                if (expected_primary_reset is None or s["rate_limits"].get("primary", {}).get("resets_at") == expected_primary_reset)
                and (expected_secondary_reset is None or s["rate_limits"].get("secondary", {}).get("resets_at") == expected_secondary_reset)]
    selected_snapshot = coherent[-1] if coherent else None
    selected = selected_snapshot["rate_limits"] if selected_snapshot else {}
    resets = sorted({(s["rate_limits"].get("primary", {}).get("resets_at"),
                      s["rate_limits"].get("secondary", {}).get("resets_at")) for s in snapshots})
    return selected, {"snapshot_count": len(snapshots), "coherent_count": len(coherent), "reset_pairs": resets,
                      "ambiguous": len(resets) > 1, "snapshots": snapshots,
                      "selected_snapshot": selected_snapshot,
                      "selection_reason": "last snapshot matching locked primary and weekly reset" if selected_snapshot else "no snapshot matched locked reset pair",
                      "session_file": str(matches[0]), "session_match_count": len(matches), "resolved": resolved}


def schedule(models, repetitions, mode, phase=0):
    a, b = (models[1], models[0]) if phase % 2 else models
    pairs = []
    for rep in range(1, repetitions + 1):
        ordered_tasks = TASKS if rep % 2 else tuple(reversed(TASKS))
        for task_index, task in enumerate(ordered_tasks):
            if mode == "paired_batches":
                block = [a, a, b, b] if task_index % 2 == 0 else [b, b, a, a]
            else:
                block = [a, b, b, a] if mode == "abba" else [a, b]
            for model in block:
                pairs.append((rep, task, model))
    return pairs


def fresh_read_meta(config):
    return {"ok": False, "snapshot": None, "error": "fresh read disabled",
            "settle_seconds": config.get("post_run_settle_seconds", 0),
            "limit_id": config.get("fresh_read_limit_id", "codex"),
            "requested_at": None, "read_at": None, "raw_result": None}


def run_one(run_dir, task, model, effort, expected_primary_reset, expected_secondary_reset, config):
    task_dir = ROOT / "tasks" / task
    work_dir = run_dir / "work"
    work_dir.mkdir()
    source_input = next(task_dir.glob("input.*"))
    shutil.copy2(source_input, work_dir / source_input.name)
    output = run_dir / "answer.json"
    prompt = (task_dir / "prompt.txt").read_text()
    cmd = [
        "codex", "exec", "-", "--json", "--ignore-user-config",
        "--ignore-rules", "--skip-git-repo-check", "--sandbox", "read-only",
        "--model", model,
        "-c", f'model_reasoning_effort="{effort}"',
        "--output-schema", str(ROOT / "schemas" / "result.schema.json"),
        "--output-last-message", str(output), "--cd", str(work_dir)
    ]
    started = time.monotonic()
    proc = subprocess.run(cmd, input=prompt, text=True, capture_output=True)
    duration = time.monotonic() - started
    events = []
    parse_errors = 0
    for line in proc.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            parse_errors += 1
    (run_dir / "events.jsonl").write_text(proc.stdout)
    (run_dir / "stderr.txt").write_text(proc.stderr)
    token, _ = metrics(events)
    rate, rate_meta = {}, empty_rate_meta("rollout polling not started")
    for _ in range(12):
        rate, rate_meta = rate_limit_for_events(events, expected_primary_reset, expected_secondary_reset)
        if rate and rate_meta["coherent_count"] > 0 and rate_meta["snapshot_count"] >= 2:
            break
        time.sleep(0.5)
    (run_dir / "rate-limit-snapshots.raw.json").write_text(json.dumps(rate_meta["snapshots"], indent=2))
    (run_dir / "selected-rate-limit.json").write_text(json.dumps({"selected": rate_meta["selected_snapshot"],
                                                                    "reason": rate_meta["selection_reason"]}, indent=2))
    (run_dir / "resolved-session.json").write_text(json.dumps(rate_meta["resolved"], indent=2))
    # Fresh, non-generative backend read taken after an optional settle delay. This is
    # an independent settlement cross-check, kept separate from the immutable in-run
    # rollout snapshots above; it starts no model turn and consumes no model quota.
    if config.get("fresh_read_enabled", True):
        fresh = quota_probe.read_account_rate_limits(
            settle_seconds=config.get("post_run_settle_seconds", 0),
            timeout=config.get("fresh_read_timeout_seconds", 25),
            limit_id=config.get("fresh_read_limit_id", "codex"))
    else:
        fresh = fresh_read_meta(config)
    (run_dir / "fresh-rate-limit.post-run.json").write_text(json.dumps(fresh, indent=2))
    try:
        actual = json.loads(output.read_text())
        expected = json.loads((task_dir / "expected.json").read_text())
        correct = actual == expected
    except (OSError, json.JSONDecodeError):
        actual, correct = None, False
    command_events = [e for e in events if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "command_execution"]
    agent_messages = [e for e in events if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "agent_message"]
    counts = {"command_executions": len(command_events), "agent_messages": len(agent_messages),
              "events": len(events), "parse_errors": parse_errors,
              "thread_id": next((e.get("thread_id") for e in events if e.get("type") == "thread.started"), None)}
    return proc.returncode, duration, actual, correct, token, rate, counts, rate_meta, fresh


def main():
    parser = argparse.ArgumentParser(description="GPT-5.5 vs GPT-5.6 Sol Codex quota benchmark")
    parser.add_argument("--dry-run", action="store_true", help="show the schedule without consuming quota")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--skip-idle-check", action="store_true", help="skip idle drift preflight when launched from an active Codex turn")
    args = parser.parse_args()
    config = json.loads((ROOT / "config.json").read_text())
    state_path = ROOT / "experiment_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"next_phase": 0, "completed_experiments": []}
    phase = int(state.get("next_phase", 0))
    plan = schedule(config["models"], config["repetitions"], config["schedule"], phase)
    print(f"Planned runs: {len(plan)} (3 tasks, paired two-run batches, order phase {phase})")
    if args.dry_run:
        for index, (rep, task, model) in enumerate(plan, 1):
            print(f"{index:02d}  rep={rep}  task={task:12s} model={model}")
        return 0
    if not args.yes and input("This consumes Codex quota. Type RUN to continue: ").strip() != "RUN":
        print("Cancelled.")
        return 1

    locked_pair = state.get("locked_reset_pair")
    if locked_pair and locked_pair.get("primary", 0) <= int(time.time()):
        locked_pair = None
    baseline_start = last_known_rate_limit(locked_pair.get("primary") if locked_pair else None,
                                           locked_pair.get("secondary") if locked_pair else None)
    if not baseline_start.get("rate_limits"):
        print("Preflight failed: no current Codex rate-limit snapshot was found.")
        return 2
    idle_seconds = 0 if args.skip_idle_check else int(config.get("idle_check_seconds", 0))
    if idle_seconds:
        print(f"Preflight idle-drift check: waiting {idle_seconds}s...", flush=True)
        time.sleep(idle_seconds)
    baseline_end = last_known_rate_limit(locked_pair.get("primary") if locked_pair else None,
                                         locked_pair.get("secondary") if locked_pair else None)
    start_rate, end_rate = baseline_start.get("rate_limits", {}), baseline_end.get("rate_limits", {})
    drift_fields = ("used_percent", "resets_at")
    drift = any(start_rate.get(lane, {}).get(field) != end_rate.get(lane, {}).get(field)
                for lane in ("primary", "secondary") for field in drift_fields)
    if drift:
        print("Preflight failed: shared quota changed while idle. Stop other Codex/agentic activity and retry.")
        return 3

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    result_root = ROOT / "results" / stamp
    result_root.mkdir(parents=True)
    manifest = {"created_at": now(), "phase": phase, "config": config, "plan": [
        {"run": i, "repetition": rep, "task": task, "model": model}
        for i, (rep, task, model) in enumerate(plan, 1)],
        "baseline_start": baseline_start, "baseline_end": baseline_end, "idle_drift": drift,
        "idle_check_skipped": args.skip_idle_check,
        "status": "running"}
    artifact_files = [ROOT / "config.json", ROOT / "schemas" / "result.schema.json"]
    for task in TASKS:
        artifact_files.extend([ROOT / "tasks" / task / "prompt.txt", next((ROOT / "tasks" / task).glob("input.*")),
                               ROOT / "tasks" / task / "expected.json"])
    manifest["artifact_sha256"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in artifact_files}
    (result_root / "manifest.json").write_text(json.dumps(manifest, indent=2))
    rows = []
    prior_rate = end_rate
    prior_fresh = {}
    completed = False
    for index, (rep, task, model) in enumerate(plan, 1):
        primary_before = prior_rate.get("primary", {})
        secondary_before = prior_rate.get("secondary", {})
        used_before = primary_before.get("used_percent")
        weekly_used_before = secondary_before.get("used_percent")
        guard = config.get("guard_band_percent", 0)
        if used_before is not None and used_before >= config["max_used_percent"] - guard:
            print(f"Safety stop: five-hour usage reached {used_before}% used.")
            break
        if weekly_used_before is not None and weekly_used_before >= config.get("max_weekly_used_percent", 100) - guard:
            print(f"Safety stop: weekly usage reached {weekly_used_before}% used.")
            break
        run_dir = result_root / f"{index:02d}-{task}-{model}"
        run_dir.mkdir()
        print(f"[{index}/{len(plan)}] {model} | {task} | rep {rep}", flush=True)
        code, seconds, answer, correct, token, rate, counts, rate_meta, fresh = run_one(
            run_dir, task, model, config["reasoning_effort"],
            primary_before.get("resets_at"), secondary_before.get("resets_at"), config)
        primary_after = rate.get("primary", {})
        secondary_after = rate.get("secondary", {})
        used_after = primary_after.get("used_percent")
        reset_before = primary_before.get("resets_at")
        reset_after = primary_after.get("resets_at")
        same_window = bool(reset_before is not None and reset_after is not None and reset_before == reset_after)
        delta = round(used_after - used_before, 4) if same_window and used_after is not None and used_before is not None else None
        weekly_before = secondary_before.get("used_percent")
        weekly_after = secondary_after.get("used_percent")
        weekly_reset_before = secondary_before.get("resets_at")
        weekly_reset_after = secondary_after.get("resets_at")
        same_week = bool(weekly_reset_before is not None and weekly_reset_after is not None and weekly_reset_before == weekly_reset_after)
        delta_weekly = round(weekly_after - weekly_before, 4) if same_week and weekly_after is not None and weekly_before is not None else None
        input_tokens = token.get("input_tokens")
        cached_tokens = token.get("cached_input_tokens")
        uncached_tokens = input_tokens - cached_tokens if input_tokens is not None and cached_tokens is not None else None
        # Fresh post-run backend read: diagnostic settlement cross-check, never the
        # authoritative attribution anchor. Kept alongside the in-run snapshot so
        # disagreements are visible rather than hidden.
        fresh_snap = fresh.get("snapshot") or {}
        fresh_primary = fresh_snap.get("primary", {})
        fresh_secondary = fresh_snap.get("secondary", {})
        fresh_used_5h = fresh_primary.get("used_percent")
        fresh_used_weekly = fresh_secondary.get("used_percent")
        fresh_reset_5h = fresh_primary.get("resets_at")
        fresh_reset_weekly = fresh_secondary.get("resets_at")
        fresh_window_matches_inrun = bool(
            fresh.get("ok") and quota_probe.windows_match(fresh_reset_5h, reset_after)
            and quota_probe.windows_match(fresh_reset_weekly, weekly_reset_after))
        fresh_vs_inrun_5h = (round(fresh_used_5h - used_after, 4)
                             if fresh_used_5h is not None and used_after is not None else None)
        fresh_vs_inrun_weekly = (round(fresh_used_weekly - weekly_after, 4)
                                 if fresh_used_weekly is not None and weekly_after is not None else None)
        prior_fresh_5h = prior_fresh.get("primary", {}).get("used_percent")
        prior_fresh_reset_5h = prior_fresh.get("primary", {}).get("resets_at")
        prior_fresh_weekly = prior_fresh.get("secondary", {}).get("used_percent")
        prior_fresh_reset_weekly = prior_fresh.get("secondary", {}).get("resets_at")
        fresh_delta_5h = (round(fresh_used_5h - prior_fresh_5h, 4)
                          if fresh_used_5h is not None and prior_fresh_5h is not None
                          and quota_probe.windows_match(fresh_reset_5h, prior_fresh_reset_5h) else None)
        fresh_delta_weekly = (round(fresh_used_weekly - prior_fresh_weekly, 4)
                              if fresh_used_weekly is not None and prior_fresh_weekly is not None
                              and quota_probe.windows_match(fresh_reset_weekly, prior_fresh_reset_weekly) else None)
        row = {
            "timestamp": now(), "run": index, "repetition": rep, "task": task, "model": model,
            "exit_code": code, "correct": correct, "duration_seconds": round(seconds, 3),
            "used_5h_before": used_before, "used_5h_after": used_after, "delta_5h": delta,
            "reset_5h": primary_after.get("resets_at"),
            "used_weekly_before": weekly_before, "used_weekly_after": weekly_after,
            "delta_weekly": delta_weekly, "reset_weekly": weekly_reset_after,
            "input_tokens": input_tokens, "cached_input_tokens": cached_tokens, "uncached_input_tokens": uncached_tokens,
            "output_tokens": token.get("output_tokens"), "reasoning_output_tokens": token.get("reasoning_output_tokens"),
            "command_executions": counts["command_executions"], "agent_messages": counts["agent_messages"],
            "event_count": counts["events"],
            "parse_errors": counts["parse_errors"], "thread_id": counts["thread_id"],
            "rate_snapshot_count": rate_meta["snapshot_count"],
            "rate_coherent_count": rate_meta["coherent_count"],
            "rate_ambiguous": rate_meta["ambiguous"],
            "rate_reset_pairs": json.dumps(rate_meta["reset_pairs"]),
            "rate_session_file": rate_meta["session_file"],
            "rate_session_match_count": rate_meta["session_match_count"],
            "resolved_model": rate_meta["resolved"].get("model"),
            "resolved_effort": rate_meta["resolved"].get("effort"),
            "cli_version": rate_meta["resolved"].get("cli_version"),
            "session_id": rate_meta["resolved"].get("session_id"),
            "model_context_window": rate_meta["resolved"].get("model_context_window"),
            "fresh_read_ok": bool(fresh.get("ok")),
            "fresh_read_error": fresh.get("error"),
            "fresh_settle_seconds": fresh.get("settle_seconds"),
            "fresh_limit_id": fresh_snap.get("limit_id"),
            "fresh_used_5h": fresh_used_5h, "fresh_used_weekly": fresh_used_weekly,
            "fresh_reset_5h": fresh_reset_5h, "fresh_reset_weekly": fresh_reset_weekly,
            "fresh_window_matches_inrun": fresh_window_matches_inrun,
            "fresh_vs_inrun_5h": fresh_vs_inrun_5h, "fresh_vs_inrun_weekly": fresh_vs_inrun_weekly,
            "fresh_delta_5h": fresh_delta_5h, "fresh_delta_weekly": fresh_delta_weekly,
            "fresh_reset_credit_count": fresh_snap.get("reset_credit_available_count"),
            "answer": json.dumps(answer, separators=(",", ":")) if answer is not None else ""
        }
        row["model_verified"] = row["resolved_model"] == model and row["resolved_effort"] == config["reasoning_effort"]
        row["tool_count_expected"] = counts["command_executions"] == config.get("expected_command_executions", 1)
        row["message_count_expected"] = counts["agent_messages"] <= config.get("max_agent_messages", 2)
        row["model_calls_expected"] = rate_meta["snapshot_count"] == config.get("expected_model_calls", 2)
        row["input_token_in_expected_band"] = bool(input_tokens is not None and
                                                    config.get("input_token_warning_min", 0) <= input_tokens <=
                                                    config.get("input_token_warning_max", 10**12))
        row["quota_valid"] = bool(code == 0 and rate and same_window and same_week and not rate_meta["ambiguous"])
        row["telemetry_valid"] = bool(row["quota_valid"] and row["model_verified"] and counts["parse_errors"] == 0)
        rows.append(row)
        (result_root / "results.json").write_text(json.dumps(rows, indent=2))
        with (result_root / "results.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=row.keys())
            writer.writeheader(); writer.writerows(rows)
        prior_rate = rate or prior_rate
        if fresh.get("ok") and fresh_snap:
            prior_fresh = fresh_snap
        print(f"  correct={correct}  5h used={used_after}%  delta={delta}  output_tokens={token.get('output_tokens')}")
        if fresh.get("ok") and fresh_vs_inrun_5h not in (None, 0):
            print(f"  fresh post-run read disagrees with in-run 5h snapshot by {fresh_vs_inrun_5h:+} pts (diagnostic)")
        elif not fresh.get("ok"):
            print(f"  fresh post-run read unavailable: {fresh.get('error')}")
        if code != 0:
            print(f"Fatal run error: inspect {run_dir / 'stderr.txt'}")
            break
        if not rate:
            print("  quota snapshot did not match the locked window; run retained but quota marked invalid")
        elif not same_window:
            print("Window reset detected: stopping to keep quota deltas comparable.")
            break
        if index < len(plan):
            time.sleep(config["pause_seconds"])
    completed = len(rows) == len(plan) and all(r["exit_code"] == 0 for r in rows)
    manifest["status"] = "complete" if completed else "stopped"
    manifest["completed_at"] = now()
    (result_root / "manifest.json").write_text(json.dumps(manifest, indent=2))
    if completed:
        state["next_phase"] = 1 - phase
        state.setdefault("completed_experiments", []).append({"result": stamp, "phase": phase})
        valid_resets = [(r.get("reset_5h"), r.get("reset_weekly")) for r in rows
                        if r.get("reset_5h") is not None and r.get("reset_weekly") is not None]
        if valid_resets:
            primary_reset, weekly_reset = max(set(valid_resets), key=valid_resets.count)
            state["locked_reset_pair"] = {"primary": primary_reset, "secondary": weekly_reset}
        state_path.write_text(json.dumps(state, indent=2))
    print(f"\nResults: {result_root}")
    print("\nFinal summary\n-------------")
    subprocess.run([sys.executable, str(ROOT / "report.py"), str(result_root)], check=False)
    print("\nSanitized evidence bundle")
    subprocess.run([sys.executable, str(ROOT / "export_evidence.py"), str(result_root)], check=False)
    return 0 if completed else 4


if __name__ == "__main__":
    raise SystemExit(main())
