#!/usr/bin/env python3
import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent

parser = argparse.ArgumentParser(description="Run multiple complete Codex benchmark batteries sequentially")
parser.add_argument("--count", type=int, default=2)
parser.add_argument("--skip-idle-check", action="store_true")
args = parser.parse_args()

if args.count < 1:
    raise SystemExit("--count must be at least 1")

for battery in range(1, args.count + 1):
    print(f"\n=== BATTERY {battery}/{args.count} ===", flush=True)
    cmd = [sys.executable, str(ROOT / "run_benchmark.py"), "--yes"]
    if args.skip_idle_check:
        cmd.append("--skip-idle-check")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print(f"Battery {battery} stopped with exit code {result.returncode}; remaining batteries cancelled.")
        raise SystemExit(result.returncode)

print("\nAll requested batteries completed.")
