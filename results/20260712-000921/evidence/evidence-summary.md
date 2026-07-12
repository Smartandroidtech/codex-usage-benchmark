# Codex model benchmark evidence — 20260712-000921

Runs: 12

| Model | Correct | Input | Cached | Uncached | Output | Reasoning | Duration | Commands |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| gpt-5.5 | 6/6 | 165883 | 113152 | 52731 | 3644 | 2898 | 103.477s | 6 |
| gpt-5.6-sol | 6/6 | 165247 | 78336 | 86911 | 1937 | 1041 | 71.246s | 6 |

Five-hour endpoint change: 7.0 percentage points.
Weekly endpoint change: 1.0 percentage points.

Sanitization preserves thread/session identifiers for diagnostic correlation, while replacing home paths, email-like strings, bearer tokens, cookies and authorization fields. Raw logs remain local beside this evidence folder.
