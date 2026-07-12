#!/usr/bin/env python3
import json
import pathlib
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent
state = json.loads((ROOT / "experiment_state.json").read_text())
experiments = []
prior_endpoints = {}

for item in state.get("completed_experiments", []):
    result_dir = ROOT / "results" / item["result"]
    if not (result_dir / "results.json").exists():
        continue
    rows = json.loads((result_dir / "results.json").read_text())
    manifest = json.loads((result_dir / "manifest.json").read_text())
    snapshot_cache = {}
    pair_counts = Counter()
    for row in rows:
        run_dir = next(p for p in result_dir.iterdir() if p.is_dir() and p.name.startswith(f"{row['run']:02d}-"))
        snapshots = json.loads((run_dir / "rate-limit-snapshots.raw.json").read_text())
        snapshot_cache[row["run"]] = snapshots
        for snapshot in snapshots:
            rate = snapshot["rate_limits"]
            pair_counts[(rate.get("primary", {}).get("resets_at"), rate.get("secondary", {}).get("resets_at"))] += 1
    target = pair_counts.most_common(1)[0][0] if pair_counts else (None, None)
    baseline = manifest.get("baseline_end", {}).get("rate_limits", {})
    baseline_pair = (baseline.get("primary", {}).get("resets_at"), baseline.get("secondary", {}).get("resets_at"))
    current = baseline.get("primary", {}).get("used_percent") if baseline_pair == target else prior_endpoints.get(target)
    batch_rows = []
    index = 0
    while index + 1 < len(rows):
        a, b = rows[index], rows[index + 1]
        if a["task"] != b["task"] or a["model"] != b["model"]:
            index += 1
            continue
        start = current
        seen = 0
        for row in (a, b):
            snapshots = snapshot_cache[row["run"]]
            matches = [s for s in snapshots if
                       (s["rate_limits"].get("primary", {}).get("resets_at"),
                        s["rate_limits"].get("secondary", {}).get("resets_at")) == target]
            if matches:
                current = matches[-1]["rate_limits"]["primary"]["used_percent"]
                seen += 1
        delta = current - start if start is not None and current is not None and seen else None
        batch_rows.append({"task": a["task"], "model": a["model"], "delta_5h": delta,
                           "target_snapshots_seen": seen})
        index += 2
    prior_endpoints[target] = current
    experiments.append({"result": item["result"], "phase": item["phase"], "batches": batch_rows,
                        "reset_pair": {"primary": target[0], "secondary": target[1]},
                        "endpoint_used_percent": current})

totals = {}
for exp in experiments:
    for batch in exp["batches"]:
        if batch["delta_5h"] is not None:
            totals[batch["model"]] = totals.get(batch["model"], 0) + batch["delta_5h"]

summary = {"method": "per-experiment dominant-reset reconciliation",
           "experiments": experiments, "model_totals": totals}
target_dir = ROOT / "results" / "combined"
target_dir.mkdir(exist_ok=True)
(target_dir / "combined-findings.json").write_text(json.dumps(summary, indent=2))

lines = ["# Combined benchmark findings", "", "Each experiment uses its dominant reset pair.", "",
         "| Experiment | Phase | Reset | Task | Model | 5h points | Matching snapshots |",
         "|---|---:|---|---|---|---:|---:|"]
for exp in experiments:
    for batch in exp["batches"]:
        lines.append(f"| {exp['result']} | {exp['phase']} | {exp['reset_pair']['primary']} | {batch['task']} | {batch['model']} | {batch['delta_5h']} | {batch['target_snapshots_seen']} |")
lines += ["", "## Totals", ""]
for model, total in sorted(totals.items()):
    lines.append(f"- {model}: {total} percentage points")
lines += ["", "This reconciliation follows the dominant reset pair within each experiment. It is diagnostic evidence and retains integer-percentage quantization uncertainty."]
(target_dir / "combined-findings.md").write_text("\n".join(lines) + "\n")
print(target_dir / "combined-findings.md")
