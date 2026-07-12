# Repository guidance

## Scope

This repository measures observed Codex quota usage for `gpt-5.5` and `gpt-5.6-sol` at `medium` reasoning effort. Preserve the experimental design: deterministic tasks, isolated sessions, balanced model order, and in-run quota attribution.

## Safety

- Never commit raw rollout files, unsanitized result directories, credentials, cookies, authorization headers, personal paths, or email addresses.
- Publish evidence only through `export_evidence.py`; verify that filenames contain `.sanitized.` or belong to an exported `evidence/` directory.
- Keep thread and session identifiers in sanitized evidence. They are intentionally retained for auditability.
- Do not run the benchmark while another Codex session is consuming the same quota window.
- Do not start a quota-consuming test unless the user explicitly requests it.

## Validation

Before committing benchmark changes, run:

```bash
python3 tests/test_quota_probe.py
python3 run_benchmark.py --dry-run
```

After changing the website, serve `site/` locally and verify both `index.html` and `methodology.html` at desktop and mobile widths.

## Results

- `results/combined/` is the canonical aggregate report.
- Public experiment artifacts live only in the eight whitelisted sanitized `evidence/` directories.
- Treat integer percentage-point readings as quantized observations, not exact billing multipliers.
