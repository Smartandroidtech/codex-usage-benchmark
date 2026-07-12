#!/usr/bin/env python3
import json
import pathlib
import statistics
import sys

root = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else max((pathlib.Path(__file__).parent / "results").iterdir())
rows = json.loads((root / "results.json").read_text())
successful = [r for r in rows if r.get("exit_code") == 0]

def nums(sample, key):
    return [r[key] for r in sample if r.get(key) is not None]

def batches(sample):
    result = []
    i = 0
    while i + 1 < len(sample):
        a, b = sample[i], sample[i + 1]
        same = a["task"] == b["task"] and a["model"] == b["model"]
        same5 = a.get("reset_5h") == b.get("reset_5h") and a.get("used_5h_before") is not None and b.get("used_5h_after") is not None
        samew = a.get("reset_weekly") == b.get("reset_weekly") and a.get("used_weekly_before") is not None and b.get("used_weekly_after") is not None
        if same:
            reasons = []
            if not a.get("quota_valid", False) or not b.get("quota_valid", False): reasons.append("quota_invalid_or_ambiguous")
            if not a.get("model_verified", False) or not b.get("model_verified", False): reasons.append("model_or_effort_unverified")
            if a.get("parse_errors", 0) or b.get("parse_errors", 0): reasons.append("json_parse_errors")
            result.append({"task": a["task"], "model": a["model"],
                           "delta_5h": b["used_5h_after"] - a["used_5h_before"] if same5 else None,
                           "delta_weekly": b["used_weekly_after"] - a["used_weekly_before"] if samew else None,
                           "valid": not reasons and same5 and samew, "reasons": reasons})
            i += 2
        else:
            i += 1
    return result

print(f"Report: {root.name}")
print(f"Technically successful runs: {len(successful)}/{len(rows)}\n")
for model in sorted({r["model"] for r in successful}):
    sample = [r for r in successful if r["model"] == model]
    inp, cached = nums(sample, "input_tokens"), nums(sample, "cached_input_tokens")
    uncached, out, reason = nums(sample, "uncached_input_tokens"), nums(sample, "output_tokens"), nums(sample, "reasoning_output_tokens")
    duration, tools = nums(sample, "duration_seconds"), nums(sample, "command_executions")
    print(model)
    print(f"  accuracy: {sum(bool(r.get('correct')) for r in sample)}/{len(sample)}")
    print(f"  input total/cached/uncached: {sum(inp)}/{sum(cached)}/{sum(uncached)}")
    print(f"  output/reasoning: {sum(out)}/{sum(reason)}")
    print(f"  duration mean: {statistics.mean(duration):.2f}s; command executions: {sum(tools)}")

batch_rows = batches(successful)
print("\nPaired two-run batches (integer percentage points; each delta has about ±1 point quantization uncertainty):")
for b in batch_rows:
    status = "valid" if b["valid"] else "EXCLUDED:" + ",".join(b["reasons"] or ["window_mismatch"])
    print(f"  {b['task']:12s} {b['model']:12s} 5h={b['delta_5h']} weekly={b['delta_weekly']} [{status}]")

models = sorted({r["model"] for r in successful})
totals = {}
valid_tasks_by_model = {model: {b["task"] for b in batch_rows if b["model"] == model and b["valid"]} for model in models}
common_valid_tasks = set.intersection(*(valid_tasks_by_model[m] for m in models)) if models else set()
print(f"Comparable valid tasks present for both models: {', '.join(sorted(common_valid_tasks)) or 'none'}")
for model in models:
    ds = [b["delta_5h"] for b in batch_rows if b["model"] == model and b["valid"]
          and b["task"] in common_valid_tasks and b["delta_5h"] is not None]
    totals[model] = sum(ds) if ds else None
    print(f"{model} comparable batch 5h total: {totals[model]}" if totals[model] is not None else f"{model} comparable batch 5h total: unavailable")
if len(common_valid_tasks) < 2:
    print("Headline comparison: insufficient valid task coverage")
elif len(models) == 2 and totals[models[0]] is not None and totals[models[1]] is not None and totals[models[0]] > 0:
    print(f"Batch quota ratio {models[1]} / {models[0]}: {totals[models[1]] / totals[models[0]]:.3f}x")

print("\nSensitivity including ambiguous/invalid batches (diagnostic only):")
for model in models:
    ds = [b["delta_5h"] for b in batch_rows if b["model"] == model and b["delta_5h"] is not None]
    print(f"  {model}: signed total={sum(ds) if ds else 'unavailable'}")

if successful:
    first, last = successful[0], successful[-1]
    if first.get("reset_weekly") == last.get("reset_weekly") and first.get("used_weekly_before") is not None and last.get("used_weekly_after") is not None:
        print(f"Weekly endpoint change for the whole benchmark: {last['used_weekly_after'] - first['used_weekly_before']} points")

fresh_rows = [r for r in successful if r.get("fresh_read_ok")]
if fresh_rows:
    print("\nFresh post-run backend reads (diagnostic only; in-run rollout snapshot stays authoritative):")
    settles = sorted({r.get("fresh_settle_seconds") for r in fresh_rows})
    disagreements = [r for r in fresh_rows if r.get("fresh_vs_inrun_5h") not in (None, 0)]
    window_mismatch = [r for r in fresh_rows if not r.get("fresh_window_matches_inrun")]
    print(f"  fresh reads: {len(fresh_rows)}/{len(successful)} runs; settle seconds used: {settles}")
    print(f"  runs where fresh 5h disagrees with in-run snapshot: {len(disagreements)}")
    print(f"  runs where fresh read fell in a different reset window than in-run: {len(window_mismatch)}")
    for model in models:
        ds = [r["fresh_delta_5h"] for r in fresh_rows if r["model"] == model and r.get("fresh_delta_5h") is not None]
        print(f"  {model}: fresh-to-fresh signed 5h total={sum(ds) if ds else 'unavailable'} over {len(ds)} chained reads")
    if disagreements:
        print("  Disagreements are expected under integer quantization and settle-window activity; they do not")
        print("  override the in-run attribution but are retained as signed diagnostics.")
elif any("fresh_read_ok" in r for r in successful):
    print("\nFresh post-run backend reads: none succeeded (see fresh_read_error in results).")

print("\nPer-task token means:")
for task in sorted({r["task"] for r in successful}):
    for model in models:
        sample = [r for r in successful if r["task"] == task and r["model"] == model]
        if sample:
            print(f"  {task:12s} {model:12s} output={statistics.mean(nums(sample, 'output_tokens')):.1f} reasoning={statistics.mean(nums(sample, 'reasoning_output_tokens')):.1f} duration={statistics.mean(nums(sample, 'duration_seconds')):.2f}s")
