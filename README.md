# Codex Usage Benchmark

An independent, reproducible comparison of observed Codex five-hour quota usage between `gpt-5.5` and `gpt-5.6-sol`, both at `medium` reasoning effort.

**Live results:** [codex-window-benchmark.smart97.chatgpt.site](https://codex-window-benchmark.smart97.chatgpt.site)

## Headline finding

Across 8 complete counterbalanced suites and 96 total runs:

| Metric | GPT-5.5 | GPT-5.6 Sol | GPT-5.6 difference |
|---|---:|---:|---:|
| Observed 5h quota | 29 points | 34 points | **+17.2%** |
| Input tokens | 1,408,831 | 1,350,925 | −4.1% |
| Uncached input | 397,247 | 423,949 | +6.7% |
| Output tokens | 27,762 | 15,901 | −42.7% |
| Reasoning tokens | 19,537 | 8,525 | −56.4% |
| Total duration | 744.6 s | 557.9 s | −25.1% |
| Correct runs | 45/48 | 47/48 | +4.2 pp |

GPT-5.6 Sol was substantially more efficient in output, reasoning tokens, and elapsed time, but consumed five more integer percentage points from the observed five-hour window. This is evidence of a measurable aggregate difference in these runs—not proof of an exact internal billing multiplier.

See the [combined findings](results/combined/combined-findings.md) and the [methodology page](https://codex-window-benchmark.smart97.chatgpt.site/methodology.html).

## Experimental design

- 3 deterministic task families: `aggregate`, `dependencies`, and `schedule`.
- 2 models at the same `medium` reasoning effort.
- 2 passes per task and model, producing 12 runs per suite.
- 8 complete suites: 4 in each model order.
- Fresh process and session for every run.
- Exact expected-output validation.
- Token, cache, duration, correctness, resolved model/effort, and quota telemetry retained.

The execution pattern uses contiguous counterbalanced batches (`AABB / BBAA / AABB`). The starting model alternates automatically between complete experiments.

## Measurement

The primary quota source is the in-run telemetry field:

```text
token_count.rate_limits.primary.used_percent
```

It represents the primary 300-minute window associated with the run. Fresh post-run reads from `codex app-server` are retained as diagnostics, but are not used as the attribution anchor because account-global probes showed alternating reset pools. Percentage readings are integers and therefore carry quantization uncertainty.

## Requirements

- macOS or Linux
- Python 3.11+
- Codex CLI authenticated with access to both benchmarked models

No Python packages outside the standard library are required.

## Run the benchmark

Inspect the plan without consuming quota:

```bash
python3 run_benchmark.py --dry-run
```

Start one complete suite:

```bash
./run.command
```

Type `RUN` when prompted. Do not use other Codex or ChatGPT agent surfaces while the suite is running, because they may share the same quota window.

Generate or refresh reports:

```bash
python3 report.py
python3 summarize_experiments.py
```

Export sanitized evidence:

```bash
python3 export_evidence.py
```

## Validation

Run offline tests and inspect the execution plan:

```bash
python3 tests/test_quota_probe.py
python3 run_benchmark.py --dry-run
```

## Repository map

```text
tasks/                  deterministic inputs, prompts, and expected outputs
schemas/                result schema
tests/                  offline quota parsing and attribution tests
results/combined/       canonical aggregate findings
results/*/evidence/     sanitized evidence for the eight valid suites
site/                   public results and methodology website
run_benchmark.py        benchmark orchestrator
quota_probe.py          rate-limit telemetry reader
export_evidence.py      sanitizer and evidence exporter
report.py               per-experiment reporting
summarize_experiments.py aggregate reporting
```

## Limitations

- Quota readings are quantized to integer percentage points.
- The suite covers three deterministic task families, not every Codex workload.
- The results establish observed behavior for the tested windows and dates; they do not expose server-side accounting rules.
- Repetition across additional five-hour windows and dates would further tighten the estimate.

## Data handling

Only sanitized evidence is tracked. Personal paths, email addresses, cookies, and authorization tokens are redacted. Thread and session identifiers are intentionally retained for auditability.
