#!/usr/bin/env python3
import csv
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
result_dir = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else max((ROOT / "results").iterdir())
rows = json.loads((result_dir / "results.json").read_text())
out = result_dir / "evidence"
out.mkdir(exist_ok=True)

HOME = str(pathlib.Path.home())
SENSITIVE_KEYS = {"account_id", "user_id", "email", "authorization", "cookie"}

def redact_text(value):
    value = value.replace(HOME, "<HOME>")
    value = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~-]+", r"\1<REDACTED>", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "<EMAIL>", value)
    return value

def sanitize(value, key=""):
    if key.lower() in SENSITIVE_KEYS:
        return "<REDACTED>"
    if isinstance(value, dict):
        return {k: sanitize(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value

safe_rows = sanitize(rows)
(out / "results.sanitized.json").write_text(json.dumps(safe_rows, indent=2))
if safe_rows:
    with (out / "results.sanitized.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=safe_rows[0].keys())
        writer.writeheader(); writer.writerows(safe_rows)

event_manifest = []
manifest_source = result_dir / "manifest.json"
if manifest_source.exists():
    manifest_target = out / "manifest.sanitized.json"
    manifest_target.write_text(json.dumps(sanitize(json.loads(manifest_source.read_text())), indent=2))
    event_manifest.append({"file": manifest_target.name, "sha256": hashlib.sha256(manifest_target.read_bytes()).hexdigest()})
for run_dir in sorted(p for p in result_dir.iterdir() if p.is_dir() and (p / "events.jsonl").exists()):
    target = out / f"{run_dir.name}.events.sanitized.jsonl"
    clean_lines = []
    for line in (run_dir / "events.jsonl").read_text(errors="replace").splitlines():
        try:
            clean_lines.append(json.dumps(sanitize(json.loads(line)), separators=(",", ":")))
        except json.JSONDecodeError:
            clean_lines.append(json.dumps({"unparsed": redact_text(line)}))
    target.write_text("\n".join(clean_lines) + "\n")
    event_manifest.append({"file": target.name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
    for source_name in ("rate-limit-snapshots.raw.json", "selected-rate-limit.json",
                        "resolved-session.json", "fresh-rate-limit.post-run.json"):
        source = run_dir / source_name
        if not source.exists():
            continue
        target = out / f"{run_dir.name}.{source_name.replace('.raw', '').replace('.json', '')}.sanitized.json"
        try:
            payload = sanitize(json.loads(source.read_text()))
        except json.JSONDecodeError:
            payload = {"unparsed": redact_text(source.read_text(errors="replace"))}
        target.write_text(json.dumps(payload, indent=2))
        event_manifest.append({"file": target.name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})

models = sorted({r["model"] for r in rows})
summary = {"experiment": result_dir.name, "runs": len(rows), "models": {},
           "quota_valid_runs": sum(bool(r.get("quota_valid")) for r in rows),
           "ambiguous_rate_runs": sum(bool(r.get("rate_ambiguous")) for r in rows),
           "fresh_read_ok_runs": sum(bool(r.get("fresh_read_ok")) for r in rows),
           "fresh_vs_inrun_5h_disagreements": sum(
               1 for r in rows if r.get("fresh_read_ok") and r.get("fresh_vs_inrun_5h") not in (None, 0)),
           "fresh_window_mismatch_runs": sum(
               1 for r in rows if r.get("fresh_read_ok") and not r.get("fresh_window_matches_inrun")),
           "sanitized_evidence_files": event_manifest}
for model in models:
    sample = [r for r in rows if r["model"] == model and r.get("exit_code") == 0]
    summary["models"][model] = {
        "runs": len(sample), "correct": sum(bool(r.get("correct")) for r in sample),
        "input_tokens": sum(r.get("input_tokens") or 0 for r in sample),
        "cached_input_tokens": sum(r.get("cached_input_tokens") or 0 for r in sample),
        "uncached_input_tokens": sum(r.get("uncached_input_tokens") or 0 for r in sample),
        "output_tokens": sum(r.get("output_tokens") or 0 for r in sample),
        "reasoning_output_tokens": sum(r.get("reasoning_output_tokens") or 0 for r in sample),
        "duration_seconds": round(sum(r.get("duration_seconds") or 0 for r in sample), 3),
        "command_executions": sum(r.get("command_executions") or 0 for r in sample)
    }
summary["five_hour_endpoint_change"] = (rows[-1]["used_5h_after"] - rows[0]["used_5h_before"]
                                                if rows and rows[-1].get("used_5h_after") is not None and rows[0].get("used_5h_before") is not None else None)
summary["weekly_endpoint_change"] = (rows[-1]["used_weekly_after"] - rows[0]["used_weekly_before"]
                                             if rows and rows[-1].get("used_weekly_after") is not None and rows[0].get("used_weekly_before") is not None else None)
(out / "evidence-summary.json").write_text(json.dumps(summary, indent=2))

md = [f"# Codex model benchmark evidence — {result_dir.name}", "", f"Runs: {len(rows)}", "",
      "| Model | Correct | Input | Cached | Uncached | Output | Reasoning | Duration | Commands |",
      "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
for model, data in summary["models"].items():
    md.append(f"| {model} | {data['correct']}/{data['runs']} | {data['input_tokens']} | {data['cached_input_tokens']} | {data['uncached_input_tokens']} | {data['output_tokens']} | {data['reasoning_output_tokens']} | {data['duration_seconds']}s | {data['command_executions']} |")
md += ["", f"Five-hour endpoint change: {summary['five_hour_endpoint_change']} percentage points.",
       f"Weekly endpoint change: {summary['weekly_endpoint_change']} percentage points.", "",
       "Sanitization preserves thread/session identifiers for diagnostic correlation, while replacing home paths, email-like strings, bearer tokens, cookies and authorization fields. Raw logs remain local beside this evidence folder."]
(out / "evidence-summary.md").write_text("\n".join(md) + "\n")
print(out)
