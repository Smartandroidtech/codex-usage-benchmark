# Codex model benchmark evidence — 20260712-012414

Runs: 12

| Model | Correct | Input | Cached | Uncached | Output | Reasoning | Duration | Commands |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| gpt-5.5 | 5/6 | 165939 | 113664 | 52275 | 2566 | 1833 | 79.956s | 6 |
| gpt-5.6-sol | 5/6 | 165224 | 130560 | 34664 | 1885 | 1034 | 68.414s | 6 |

Five-hour endpoint change: 8.0 percentage points.
Weekly endpoint change: 1.0 percentage points.

Sanitization preserves thread/session identifiers for diagnostic correlation, while replacing home paths, email-like strings, bearer tokens, cookies and authorization fields. Raw logs remain local beside this evidence folder.
