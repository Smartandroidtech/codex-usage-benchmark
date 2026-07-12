# Codex model benchmark evidence — 20260712-054549

Runs: 12

| Model | Correct | Input | Cached | Uncached | Output | Reasoning | Duration | Commands |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| gpt-5.5 | 5/6 | 165908 | 113664 | 52244 | 2595 | 1851 | 69.464s | 6 |
| gpt-5.6-sol | 6/6 | 165267 | 130560 | 34707 | 2201 | 1320 | 77.209s | 6 |

Five-hour endpoint change: 10.0 percentage points.
Weekly endpoint change: 2.0 percentage points.

Sanitization preserves thread/session identifiers for diagnostic correlation, while replacing home paths, email-like strings, bearer tokens, cookies and authorization fields. Raw logs remain local beside this evidence folder.
