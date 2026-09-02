#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hongdae_baseline.config import CONTROLLERS, MODES, load_config  # noqa: E402
from hongdae_baseline.evaluator import EvaluationBlocked, run_experiment  # noqa: E402


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="Reproduce the Hongdae single-intersection baseline")
    subcommands = command.add_subparsers(dest="command", required=True)

    single = subcommands.add_parser("single", help="Evaluate one controller on one route/seed")
    single.add_argument("--mode", choices=MODES, default="corrected_baseline")
    single.add_argument("--controller", choices=CONTROLLERS, required=True)
    single.add_argument("--seed", type=int, required=True)
    single.add_argument("--route", type=Path, help="Use this fixed route instead of generating one")
    single.add_argument("--output", type=Path)

    paired = subcommands.add_parser("paired", help="Evaluate all paired controllers on shared routes")
    paired.add_argument("--mode", choices=MODES, default="corrected_baseline")
    seed_source = paired.add_mutually_exclusive_group()
    seed_source.add_argument("--seeds", type=int, nargs="+")
    seed_source.add_argument("--seed-file", type=Path, help="JSON file containing a 'seeds' list")
    paired.add_argument("--controllers", choices=CONTROLLERS, nargs="+", default=list(CONTROLLERS))
    paired.add_argument("--route", type=Path, help="Use the same supplied route for every seed")
    paired.add_argument("--output", type=Path)
    return command


def main() -> int:
    args = parser().parse_args()
    config = load_config(args.mode, ROOT)
    controllers = [args.controller] if args.command == "single" else args.controllers
    if args.command == "single":
        seeds = [args.seed]
    elif args.seed_file:
        seed_data = json.loads(args.seed_file.read_text(encoding="utf-8"))
        seeds = [int(seed) for seed in seed_data["seeds"]]
    else:
        seeds = args.seeds or [101, 202, 303]
    if len(seeds) != len(set(seeds)):
        raise ValueError("Seed list contains duplicates")
    output = args.output
    if output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = config.results_dir / f"{config.mode}_{args.command}_{stamp}"
    try:
        completed = run_experiment(config, controllers, seeds, output, args.route)
    except EvaluationBlocked as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        print(f"Failure manifest: {(output / 'run_manifest.json').resolve()}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"Failure manifest: {(output / 'run_manifest.json').resolve()}", file=sys.stderr)
        return 1
    print(f"Completed: {completed}")
    print(f"Results: {completed / 'results.csv'}")
    print(f"Manifest: {completed / 'run_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
